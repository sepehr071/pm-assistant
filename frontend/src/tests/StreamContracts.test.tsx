import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { streamMessage, type StreamHandlers } from '../lib/stream'
import { useAppStore } from '../store'
import type { Message, Settings, Usage } from '../types'
import Chat from '../pages/Chat'

// ---------------------------------------------------------------------------
// Helpers — build a fake SSE Response whose body is a ReadableStream emitting
// the given raw blocks. `streamMessage` reads `response.body.getReader()`.
// ---------------------------------------------------------------------------

function sseResponse(blocks: string[], opts: { closeCleanly?: boolean } = {}): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const b of blocks) controller.enqueue(encoder.encode(b))
      // closeCleanly defaults true. The point of the terminal-detection test
      // is that the stream *closes* (done:true on the reader) without ever
      // having emitted a `done`/`error` SSE event.
      controller.close()
    },
  })
  void opts
  return {
    ok: true,
    status: 200,
    body,
    json: async () => ({}),
  } as unknown as Response
}

function noopHandlers(over: Partial<StreamHandlers>): StreamHandlers {
  return {
    onToken: vi.fn(),
    onToolStart: vi.fn(),
    onToolRequest: vi.fn(),
    onToolResult: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
    onUsage: vi.fn(),
    ...over,
  }
}

const originalFetch = globalThis.fetch

const TEST_SETTINGS: Settings = {
  system_prompt: '',
  auto_approve_tools: [],
  yolo_mode: false,
  dangerous_always_approve: true,
  show_tool_details: false,
  language: 'en',
}

describe('streamMessage — terminal detection', () => {
  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('invokes onError when the connection ends without a terminal event', async () => {
    // Only a token block, then the stream closes. No `done`, no `error`.
    globalThis.fetch = vi.fn(
      async () => sseResponse(['event: token\ndata: {"delta":"hi"}\n\n']),
    ) as unknown as typeof fetch

    const onError = vi.fn()
    const onDone = vi.fn()
    await streamMessage(1, 'hello', noopHandlers({ onError, onDone }))

    expect(onDone).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledTimes(1)
    // The marker string must be present so Chat.tsx can surface it.
    expect(typeof onError.mock.calls[0][0]).toBe('string')
    expect(onError.mock.calls[0][0].length).toBeGreaterThan(0)
  })

  it('does NOT invoke onError when a done event terminates the stream', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        sseResponse([
          'event: token\ndata: {"delta":"hi"}\n\n',
          'event: done\ndata: {"message_id":7}\n\n',
        ]),
    ) as unknown as typeof fetch

    const onError = vi.fn()
    const onDone = vi.fn()
    await streamMessage(1, 'hello', noopHandlers({ onError, onDone }))

    expect(onError).not.toHaveBeenCalled()
    expect(onDone).toHaveBeenCalledWith(7, expect.anything())
  })

  it('does NOT invoke a second onError when an error event already fired', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        sseResponse(['event: error\ndata: {"error":"boom"}\n\n']),
    ) as unknown as typeof fetch

    const onError = vi.fn()
    await streamMessage(1, 'hello', noopHandlers({ onError }))

    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith('boom')
  })
})

describe('streamMessage — usage + truncated', () => {
  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('routes the usage event to onUsage', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        sseResponse([
          'event: usage\ndata: {"prompt_tokens":100,"completion_tokens":42,"total_tokens":142,"cost":0.0031}\n\n',
          'event: done\ndata: {"message_id":9}\n\n',
        ]),
    ) as unknown as typeof fetch

    const onUsage = vi.fn()
    await streamMessage(1, 'hi', noopHandlers({ onUsage }))

    expect(onUsage).toHaveBeenCalledTimes(1)
    expect(onUsage).toHaveBeenCalledWith({
      prompt_tokens: 100,
      completion_tokens: 42,
      total_tokens: 142,
      cost: 0.0031,
    })
  })

  it('passes truncated:true from the done payload to onDone', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        sseResponse(['event: done\ndata: {"message_id":3,"truncated":true}\n\n']),
    ) as unknown as typeof fetch

    const onDone = vi.fn()
    await streamMessage(1, 'hi', noopHandlers({ onDone }))

    expect(onDone).toHaveBeenCalledWith(3, { truncated: true })
  })

  it('tolerates unknown future SSE event types without throwing', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        sseResponse([
          'event: brand_new_event\ndata: {"whatever":1}\n\n',
          'event: done\ndata: {"message_id":1}\n\n',
        ]),
    ) as unknown as typeof fetch

    const onError = vi.fn()
    const onDone = vi.fn()
    const outcome = await streamMessage(
      1,
      'hi',
      noopHandlers({ onError, onDone }),
    )
    expect(outcome).toEqual({ ok: true })
    expect(onError).not.toHaveBeenCalled()
    expect(onDone).toHaveBeenCalled()
  })
})

describe('store — usage slice', () => {
  beforeEach(() => {
    useAppStore.setState({
      messagesByChat: {},
      usageTotalsByChat: {},
    })
  })

  it('attaches usage to the last streaming assistant message + accumulates the chat total', () => {
    const chatId = 5
    const streaming: Message = {
      id: 'assistant-x',
      role: 'assistant',
      content: 'hello',
      isStreaming: true,
    }
    useAppStore.setState({ messagesByChat: { [chatId]: [streaming] } })

    const usage: Usage = {
      prompt_tokens: 10,
      completion_tokens: 5,
      total_tokens: 15,
      cost: 0.002,
    }
    act(() => {
      useAppStore.getState().setMessageUsage(chatId, usage)
    })

    const msgs = useAppStore.getState().messagesByChat[chatId]
    expect(msgs[0].usage).toEqual(usage)
    expect(useAppStore.getState().usageTotalsByChat[chatId]).toEqual(usage)

    // A second turn accumulates into the running total.
    const usage2: Usage = {
      prompt_tokens: 20,
      completion_tokens: 10,
      total_tokens: 30,
      cost: 0.004,
    }
    const streaming2: Message = {
      id: 'assistant-y',
      role: 'assistant',
      content: 'again',
      isStreaming: true,
    }
    useAppStore.setState((s) => ({
      messagesByChat: {
        ...s.messagesByChat,
        [chatId]: [{ ...msgs[0], isStreaming: false }, streaming2],
      },
    }))
    act(() => {
      useAppStore.getState().setMessageUsage(chatId, usage2)
    })
    expect(useAppStore.getState().usageTotalsByChat[chatId]).toEqual({
      prompt_tokens: 30,
      completion_tokens: 15,
      total_tokens: 45,
      cost: 0.006,
    })
  })

  it('treats null cost as 0 in the running total without forcing a number when all turns are null', () => {
    const chatId = 6
    const streaming: Message = {
      id: 'a',
      role: 'assistant',
      content: '',
      isStreaming: true,
    }
    useAppStore.setState({ messagesByChat: { [chatId]: [streaming] } })
    act(() => {
      useAppStore.getState().setMessageUsage(chatId, {
        prompt_tokens: 1,
        completion_tokens: 1,
        total_tokens: 2,
        cost: null,
      })
    })
    expect(useAppStore.getState().usageTotalsByChat[chatId]?.cost).toBeNull()
  })

  it('marks the last streaming assistant message as truncated', () => {
    const chatId = 7
    const streaming: Message = {
      id: 'a',
      role: 'assistant',
      content: 'partial',
      isStreaming: true,
    }
    useAppStore.setState({ messagesByChat: { [chatId]: [streaming] } })
    act(() => {
      useAppStore.getState().markMessageTruncated(chatId)
    })
    expect(useAppStore.getState().messagesByChat[chatId][0].truncated).toBe(true)
  })

  it('streams tokens into and finalizes the assistant bubble after a tool result row', () => {
    const chatId = 8
    const streaming: Message = {
      id: 'a',
      role: 'assistant',
      content: 'Checking. ',
      isStreaming: true,
    }
    useAppStore.setState({ messagesByChat: { [chatId]: [streaming] } })
    act(() => {
      const s = useAppStore.getState()
      s.addToolCallMessage(chatId, {
        tool_call_id: 'c1',
        name: 'pm__create_task',
        result: 'ok',
        is_error: false,
      })
      s.updateLastAssistantDelta(chatId, 'Done.')
      s.finalizeStreamingMessage(chatId)
    })
    const [assistant, tool] = useAppStore.getState().messagesByChat[chatId]
    expect(assistant.content).toBe('Checking. Done.')
    expect(assistant.isStreaming).toBe(false)
    expect(tool.role).toBe('tool')
  })
})

// ---------------------------------------------------------------------------
// 409 turn_in_progress → toast, composer text preserved
// ---------------------------------------------------------------------------

function renderChat(chatId: number, qc?: QueryClient) {
  const client =
    qc ??
    new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/chat/${chatId}`]}>
        <Routes>
          <Route path="/chat/:id" element={<Chat />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Chat — 409 turn_in_progress', () => {
  beforeEach(() => {
    useAppStore.setState({
      settings: TEST_SETTINGS,
      toasts: [],
      messagesByChat: {},
      chats: [{ id: 42, title: 'c', createdAt: '', updatedAt: '', kind: 'user' }],
    })
  })
  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('shows a toast and keeps composer text on HTTP 409', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      // Initial history fetch (GET messages).
      if (url.endsWith('/messages') && (!init || init.method === undefined)) {
        return {
          ok: true,
          status: 200,
          json: async () => [],
          text: async () => '[]',
        } as unknown as Response
      }
      // POST a message -> 409.
      if (url.endsWith('/messages') && init?.method === 'POST') {
        return {
          ok: false,
          status: 409,
          json: async () => ({ detail: 'turn_in_progress' }),
        } as unknown as Response
      }
      return { ok: true, status: 200, json: async () => ({}), text: async () => '' } as unknown as Response
    }) as unknown as typeof fetch

    renderChat(42)

    const textarea = await screen.findByRole('textbox')
    await userEvent.type(textarea, 'my draft message')
    // Submit.
    await act(async () => {
      await userEvent.keyboard('{Enter}')
    })

    // Toast surfaced.
    await vi.waitFor(() => {
      expect(useAppStore.getState().toasts.length).toBeGreaterThan(0)
    })
    // Composer text not cleared — draft preserved.
    expect((textarea as HTMLTextAreaElement).value).toBe('my draft message')
  })
})

// ---------------------------------------------------------------------------
// onDone reconciles the React Query cache with the live store so a chat-switch
// remount re-seeds from the just-completed turn, not the pre-turn snapshot.
// ---------------------------------------------------------------------------

describe('Chat — onDone reconciles message cache', () => {
  beforeEach(() => {
    useAppStore.setState({
      settings: TEST_SETTINGS,
      toasts: [],
      messagesByChat: {},
      usageTotalsByChat: {},
      chats: [{ id: 77, title: 'c', createdAt: '', updatedAt: '', kind: 'user' }],
    })
  })
  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('writes the live store thread into the messages query cache after a turn completes', async () => {
    globalThis.fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        // Initial history fetch (GET messages) — empty pre-turn snapshot.
        if (url.endsWith('/messages') && (!init || init.method === undefined)) {
          return {
            ok: true,
            status: 200,
            json: async () => [],
            text: async () => '[]',
          } as unknown as Response
        }
        // POST a message -> stream a token then a done event.
        if (url.endsWith('/messages') && init?.method === 'POST') {
          return sseResponse([
            'event: token\ndata: {"delta":"hello there"}\n\n',
            'event: done\ndata: {"message_id":555}\n\n',
          ])
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({}),
          text: async () => '',
        } as unknown as Response
      },
    ) as unknown as typeof fetch

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    renderChat(77, qc)

    const textarea = await screen.findByRole('textbox')
    await userEvent.type(textarea, 'hi')
    await act(async () => {
      await userEvent.keyboard('{Enter}')
    })

    // The streamed assistant turn must land in the live store.
    await vi.waitFor(() => {
      const live = useAppStore.getState().messagesByChat[77] ?? []
      expect(live.some((m) => m.role === 'assistant' && m.id === 555)).toBe(true)
    })

    // And the React Query cache must now mirror that live store — otherwise a
    // chat-switch remount would re-seed setMessages() with the stale pre-turn
    // [] snapshot and drop the just-completed turn.
    const cached = qc.getQueryData<Message[]>(['messages', 77])
    expect(cached).toBeDefined()
    expect(cached).toEqual(useAppStore.getState().messagesByChat[77])
    expect(
      cached?.some((m) => m.role === 'assistant' && m.id === 555),
    ).toBe(true)
  })
})
