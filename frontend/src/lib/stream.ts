import type { InflightTool, PendingApproval, ToolResult, Usage } from '../types'

export interface DoneOptions {
  /** Backend hit the tool-step iteration cap and cut the turn short. */
  truncated?: boolean
}

export interface StreamHandlers {
  onToken(delta: string): void
  onToolStart(tool: InflightTool): void
  onToolRequest(req: PendingApproval): void
  onToolResult(res: ToolResult): void
  onDone(messageId: number, opts?: DoneOptions): void
  onError(err: string): void
  /** Per-turn token usage; fires once just before `onDone`. */
  onUsage?(usage: Usage): void
  onTitleUpdated?(title: string): void
}

/**
 * Outcome of a stream attempt. `turn_in_progress` lets callers distinguish a
 * 409 (another turn is already streaming for this chat) from a generic error
 * so they can keep the user's draft instead of clearing it.
 */
export type StreamOutcome =
  | { ok: true }
  | { ok: false; reason: 'turn_in_progress' }
  | { ok: false; reason: 'error'; message: string }

interface ParsedEvent {
  event: string
  data: string
}

function parseEventBlock(block: string): ParsedEvent | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const rawLine of block.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    const idx = line.indexOf(':')
    const field = idx === -1 ? line : line.slice(0, idx)
    let value = idx === -1 ? '' : line.slice(idx + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') dataLines.push(value)
  }
  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

/**
 * Dispatch one parsed SSE event to the matching handler. Returns `true` when
 * the event is *terminal* (`done` or `error`) so the read loop can tell a
 * clean end-of-turn apart from a connection that died mid-stream.
 */
function dispatch(
  parsed: ParsedEvent,
  handlers: StreamHandlers,
): boolean {
  let payload: unknown
  try {
    payload = JSON.parse(parsed.data)
  } catch (err) {
    handlers.onError(
      `Failed to parse SSE payload: ${(err as Error).message}`,
    )
    // A malformed payload still counts as a terminal signal — onError runs
    // the finalize path, so we must not later fire the "ended unexpectedly"
    // fallback on top of it.
    return true
  }
  const obj = (payload ?? {}) as Record<string, unknown>
  switch (parsed.event) {
    case 'token': {
      const delta = typeof obj.delta === 'string' ? obj.delta : ''
      handlers.onToken(delta)
      return false
    }
    case 'tool_call_start': {
      handlers.onToolStart({
        tool_call_id: String(obj.tool_call_id ?? ''),
        tool_name: String(obj.tool_name ?? ''),
        arguments:
          (obj.arguments as Record<string, unknown> | undefined) ?? {},
      })
      return false
    }
    case 'tool_call_request': {
      handlers.onToolRequest({
        tool_call_id: String(obj.tool_call_id ?? ''),
        tool_name: String(obj.tool_name ?? ''),
        arguments:
          (obj.arguments as Record<string, unknown> | undefined) ?? {},
      })
      return false
    }
    case 'tool_call_result': {
      handlers.onToolResult({
        tool_call_id: String(obj.tool_call_id ?? ''),
        tool_name: String(obj.tool_name ?? ''),
        result: String(obj.result ?? ''),
        is_error: Boolean(obj.is_error),
      })
      return false
    }
    case 'usage': {
      if (handlers.onUsage) {
        handlers.onUsage({
          prompt_tokens: Number(obj.prompt_tokens ?? 0),
          completion_tokens: Number(obj.completion_tokens ?? 0),
          total_tokens: Number(obj.total_tokens ?? 0),
          cost: typeof obj.cost === 'number' ? obj.cost : null,
        })
      }
      return false
    }
    case 'done': {
      const messageId =
        typeof obj.message_id === 'number' ? obj.message_id : 0
      handlers.onDone(messageId, { truncated: obj.truncated === true })
      return true
    }
    case 'error': {
      handlers.onError(String(obj.error ?? 'unknown error'))
      return true
    }
    case 'title_updated': {
      const title = typeof obj.title === 'string' ? obj.title : ''
      if (title && handlers.onTitleUpdated) handlers.onTitleUpdated(title)
      return false
    }
    default:
      // Unknown / future event types are tolerated silently — never throw.
      return false
  }
}

/**
 * Wrap user-supplied handlers so that streamed `onToken` deltas are
 * batched and flushed once per animation frame. At ~50 tok/s a naive
 * pass-through fires 50 React state updates per second; rAF coalescing
 * caps that at the display refresh rate (typically 60 Hz) and keeps the
 * MessageList re-render budget bounded. Non-token events (tool calls,
 * done, error) flush any pending text first so the order users see
 * matches the order the backend emitted.
 */
function withRafCoalesce(handlers: StreamHandlers): {
  wrapped: StreamHandlers
  finalize: () => void
} {
  let pending = ''
  let rafId: ReturnType<typeof requestAnimationFrame> | null = null
  // jsdom in vitest doesn't implement rAF; fall back to a microtask.
  const schedule =
    typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame
      : (cb: FrameRequestCallback) =>
          setTimeout(() => cb(performance.now()), 16) as unknown as number
  const cancel =
    typeof cancelAnimationFrame === 'function'
      ? cancelAnimationFrame
      : (id: number) => clearTimeout(id as unknown as ReturnType<typeof setTimeout>)

  const flush = () => {
    rafId = null
    if (!pending) return
    const out = pending
    pending = ''
    handlers.onToken(out)
  }

  const wrapped: StreamHandlers = {
    onToken(delta) {
      if (!delta) return
      pending += delta
      if (rafId === null) rafId = schedule(flush)
    },
    onToolStart(tool) {
      flush()
      handlers.onToolStart(tool)
    },
    onToolRequest(req) {
      flush()
      handlers.onToolRequest(req)
    },
    onToolResult(res) {
      flush()
      handlers.onToolResult(res)
    },
    onUsage(usage) {
      flush()
      handlers.onUsage?.(usage)
    },
    onDone(messageId, opts) {
      flush()
      handlers.onDone(messageId, opts)
    },
    onError(err) {
      flush()
      handlers.onError(err)
    },
    onTitleUpdated(title) {
      flush()
      handlers.onTitleUpdated?.(title)
    },
  }

  const finalize = () => {
    if (rafId !== null) {
      cancel(rafId as number)
      rafId = null
    }
    flush()
  }

  return { wrapped, finalize }
}

/**
 * Sentinel passed to `onError` when the SSE connection closes without any
 * terminal (`done`/`error`) event — i.e. the backend died mid-turn. Callers
 * map it to a localized "stream ended unexpectedly" message; falling through
 * to interpolation keeps the marker readable even without that mapping.
 */
export const STREAM_ENDED_UNEXPECTEDLY = 'stream_ended_unexpectedly'

export async function streamMessage(
  chatId: number,
  text: string,
  handlers: StreamHandlers,
  options?: { signal?: AbortSignal },
): Promise<StreamOutcome> {
  const { wrapped: coalesced, finalize: finalizeCoalesce } =
    withRafCoalesce(handlers)
  handlers = coalesced
  let response: Response
  try {
    response = await fetch(`/api/chats/${chatId}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ text }),
      signal: options?.signal,
    })
  } catch (err) {
    const message = (err as Error).message
    handlers.onError(message)
    return { ok: false, reason: 'error', message }
  }

  if (!response.ok) {
    let message = `Stream failed: ${response.status}`
    let detailStr: string | null = null
    try {
      const body = await response.json()
      if (body && typeof body === 'object') {
        const detail = (body as { detail?: unknown }).detail
        if (typeof detail === 'string') {
          detailStr = detail
          message = detail
        }
      }
    } catch {
      // ignore
    }
    // 409 means a turn is already streaming for this chat. Surface it as a
    // distinct outcome so the caller can keep the user's draft instead of
    // treating it as a fatal error that wipes the bubble.
    if (response.status === 409 && detailStr === 'turn_in_progress') {
      return { ok: false, reason: 'turn_in_progress' }
    }
    handlers.onError(message)
    return { ok: false, reason: 'error', message }
  }

  if (!response.body) {
    const message = 'Stream response had no body'
    handlers.onError(message)
    return { ok: false, reason: 'error', message }
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  // Track whether we saw a terminal `done`/`error` event. If the reader
  // drains without one, the connection died mid-turn and the existing error
  // path must finalize the bubble — otherwise isStreaming sticks true forever.
  let sawTerminal = false

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sepIndex: number
      while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, sepIndex)
        buffer = buffer.slice(sepIndex + 2)
        if (!block.trim()) continue
        const parsed = parseEventBlock(block)
        if (parsed && dispatch(parsed, handlers)) sawTerminal = true
      }
    }
    buffer += decoder.decode()
    const tail = buffer.trim()
    if (tail) {
      const parsed = parseEventBlock(tail)
      if (parsed && dispatch(parsed, handlers)) sawTerminal = true
    }
    if (!sawTerminal) {
      handlers.onError(STREAM_ENDED_UNEXPECTEDLY)
      return { ok: false, reason: 'error', message: STREAM_ENDED_UNEXPECTEDLY }
    }
    return { ok: true }
  } catch (err) {
    const message = (err as Error).message
    handlers.onError(message)
    return { ok: false, reason: 'error', message }
  } finally {
    finalizeCoalesce()
  }
}
