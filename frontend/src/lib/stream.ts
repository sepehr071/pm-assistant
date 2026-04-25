import type { InflightTool, PendingApproval, ToolResult } from '../types'

export interface StreamHandlers {
  onToken(delta: string): void
  onToolStart(tool: InflightTool): void
  onToolRequest(req: PendingApproval): void
  onToolResult(res: ToolResult): void
  onDone(messageId: number): void
  onError(err: string): void
}

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

function dispatch(
  parsed: ParsedEvent,
  handlers: StreamHandlers,
): void {
  let payload: unknown
  try {
    payload = JSON.parse(parsed.data)
  } catch (err) {
    handlers.onError(
      `Failed to parse SSE payload: ${(err as Error).message}`,
    )
    return
  }
  const obj = (payload ?? {}) as Record<string, unknown>
  switch (parsed.event) {
    case 'token': {
      const delta = typeof obj.delta === 'string' ? obj.delta : ''
      handlers.onToken(delta)
      break
    }
    case 'tool_call_start': {
      handlers.onToolStart({
        tool_call_id: String(obj.tool_call_id ?? ''),
        tool_name: String(obj.tool_name ?? ''),
        arguments:
          (obj.arguments as Record<string, unknown> | undefined) ?? {},
      })
      break
    }
    case 'tool_call_request': {
      handlers.onToolRequest({
        tool_call_id: String(obj.tool_call_id ?? ''),
        tool_name: String(obj.tool_name ?? ''),
        arguments:
          (obj.arguments as Record<string, unknown> | undefined) ?? {},
      })
      break
    }
    case 'tool_call_result': {
      handlers.onToolResult({
        tool_call_id: String(obj.tool_call_id ?? ''),
        tool_name: String(obj.tool_name ?? ''),
        result: String(obj.result ?? ''),
        is_error: Boolean(obj.is_error),
      })
      break
    }
    case 'done': {
      const messageId =
        typeof obj.message_id === 'number' ? obj.message_id : 0
      handlers.onDone(messageId)
      break
    }
    case 'error': {
      handlers.onError(String(obj.error ?? 'unknown error'))
      break
    }
    default:
      break
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
    onDone(messageId) {
      flush()
      handlers.onDone(messageId)
    },
    onError(err) {
      flush()
      handlers.onError(err)
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

export async function streamMessage(
  chatId: number,
  text: string,
  handlers: StreamHandlers,
  options?: { signal?: AbortSignal },
): Promise<void> {
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
    handlers.onError((err as Error).message)
    return
  }

  if (!response.ok) {
    let message = `Stream failed: ${response.status}`
    try {
      const body = await response.json()
      if (body && typeof body === 'object') {
        const detail = (body as { detail?: unknown }).detail
        if (typeof detail === 'string') message = detail
      }
    } catch {
      // ignore
    }
    handlers.onError(message)
    return
  }

  if (!response.body) {
    handlers.onError('Stream response had no body')
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

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
        if (parsed) dispatch(parsed, handlers)
      }
    }
    buffer += decoder.decode()
    const tail = buffer.trim()
    if (tail) {
      const parsed = parseEventBlock(tail)
      if (parsed) dispatch(parsed, handlers)
    }
  } catch (err) {
    handlers.onError((err as Error).message)
  } finally {
    finalizeCoalesce()
  }
}
