import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MessageList } from './MessageList'
import type { Message, Settings } from '../types'
import { useAppStore } from '../store'

const TEST_SETTINGS: Settings = {
  default_model: '',
  system_prompt: '',
  auto_approve_tools: [],
  yolo_mode: false,
  // Tool-call cards default to compact in production. Tests below verify
  // the verbose layout (full qualified name + raw status), so flip the
  // toggle on for this suite.
  show_tool_details: true,
}

describe('MessageList', () => {
  beforeEach(() => {
    useAppStore.setState({ settings: TEST_SETTINGS })
  })
  it('renders a user bubble with plain text', () => {
    const messages: Message[] = [
      { id: 1, role: 'user', content: 'hello world' },
    ]
    render(<MessageList messages={messages} />)
    const bubble = screen.getByTestId('message-user')
    expect(bubble).toHaveTextContent('hello world')
  })

  it('renders assistant markdown with formatting', () => {
    const messages: Message[] = [
      {
        id: 2,
        role: 'assistant',
        content: '**bold** text',
      },
    ]
    render(<MessageList messages={messages} />)
    const bubble = screen.getByTestId('message-assistant')
    expect(bubble.querySelector('strong')).not.toBeNull()
    expect(bubble).toHaveTextContent('bold')
  })

  it('shows a streaming cursor when assistant message isStreaming', () => {
    const messages: Message[] = [
      {
        id: 3,
        role: 'assistant',
        content: 'typing...',
        isStreaming: true,
      },
    ]
    render(<MessageList messages={messages} />)
    expect(screen.getByTestId('streaming-cursor')).toBeInTheDocument()
  })

  it('renders a tool call card under an assistant message with toolCalls', () => {
    const messages: Message[] = [
      {
        id: 4,
        role: 'assistant',
        content: '',
        toolCalls: [
          {
            id: 'call_1',
            type: 'function',
            function: {
              name: 'jira__create_issue',
              arguments: JSON.stringify({ summary: 'Bug' }),
            },
          },
        ],
      },
      {
        id: 5,
        role: 'tool',
        content: '{"ok": true}',
        toolCallId: 'call_1',
        name: 'jira__create_issue',
      },
    ]
    render(<MessageList messages={messages} />)
    const card = screen.getByTestId('tool-call-card')
    expect(card).toHaveTextContent('jira__create_issue')
    expect(card).toHaveTextContent('done')
  })

  it('skips rendering raw tool messages outside a card', () => {
    const messages: Message[] = [
      {
        id: 6,
        role: 'tool',
        content: 'orphan tool result',
        toolCallId: 'none',
      },
    ]
    render(<MessageList messages={messages} />)
    expect(screen.queryByText('orphan tool result')).toBeNull()
  })

  // Regression for the "tool card disappears between pending and success"
  // bug. addAssistantToolCall must anchor the call on the streaming
  // assistant message immediately, so the unified merge keeps the card
  // mounted when addToolCallMessage later removes the inflight slot.
  it('keeps the tool card mounted across pending → success transitions', () => {
    const chatId = 99
    const streamingAssistant: Message = {
      id: 'assistant-streaming',
      role: 'assistant',
      content: '',
      isStreaming: true,
    }
    useAppStore.setState({ messagesByChat: { [chatId]: [streamingAssistant] } })

    // 1) tool_call_start arrives — anchor on the assistant + add inflight.
    useAppStore.getState().addAssistantToolCall(chatId, {
      id: 'tc-1',
      type: 'function',
      function: {
        name: 'github__get_me',
        arguments: JSON.stringify({}),
      },
    })
    useAppStore.getState().addInflightTool(chatId, {
      tool_call_id: 'tc-1',
      tool_name: 'github__get_me',
      arguments: {},
    })

    const view = useAppStore.getState()
    const inflightForRender =
      view.inflightToolsByChat[chatId] ?? []
    const messagesForRender =
      view.messagesByChat[chatId] ?? []
    const { rerender } = render(
      <MessageList
        messages={messagesForRender}
        inflightTools={inflightForRender}
      />,
    )
    const pendingCard = screen.getByTestId('tool-call-card')
    expect(pendingCard).toHaveTextContent('github__get_me')
    expect(pendingCard).toHaveTextContent('running')

    // 2) tool_call_result lands — inflight cleared, tool message appended.
    useAppStore.getState().addToolCallMessage(chatId, {
      tool_call_id: 'tc-1',
      name: 'github__get_me',
      result: '{"login":"sepehr071"}',
      is_error: false,
    })
    const after = useAppStore.getState()
    rerender(
      <MessageList
        messages={after.messagesByChat[chatId] ?? []}
        inflightTools={after.inflightToolsByChat[chatId] ?? []}
      />,
    )
    const successCard = screen.getByTestId('tool-call-card')
    expect(successCard).toBe(pendingCard) // same DOM node — no remount
    expect(successCard).toHaveTextContent('github__get_me')
    expect(successCard).toHaveTextContent('done')
  })

  it('keeps both cards mounted across multi-tool transitions', () => {
    const chatId = 100
    const streamingAssistant: Message = {
      id: 'assistant-streaming-2',
      role: 'assistant',
      content: '',
      isStreaming: true,
    }
    useAppStore.setState({ messagesByChat: { [chatId]: [streamingAssistant] } })

    // Two tool_call_start events.
    for (const id of ['tc-a', 'tc-b']) {
      useAppStore.getState().addAssistantToolCall(chatId, {
        id,
        type: 'function',
        function: {
          name: id === 'tc-a' ? 'github__get_me' : 'github__search_repositories',
          arguments: '{}',
        },
      })
      useAppStore.getState().addInflightTool(chatId, {
        tool_call_id: id,
        tool_name: id === 'tc-a' ? 'github__get_me' : 'github__search_repositories',
        arguments: {},
      })
    }

    const initial = useAppStore.getState()
    const { rerender } = render(
      <MessageList
        messages={initial.messagesByChat[chatId] ?? []}
        inflightTools={initial.inflightToolsByChat[chatId] ?? []}
      />,
    )
    // Two cards merged into a group when count > 1.
    expect(
      screen.getByTestId('tool-group-card'),
    ).toHaveTextContent('2 tools')

    // Resolve the first tool only — group should still show one running.
    useAppStore.getState().addToolCallMessage(chatId, {
      tool_call_id: 'tc-a',
      name: 'github__get_me',
      result: '{"login":"x"}',
      is_error: false,
    })
    const mid = useAppStore.getState()
    rerender(
      <MessageList
        messages={mid.messagesByChat[chatId] ?? []}
        inflightTools={mid.inflightToolsByChat[chatId] ?? []}
      />,
    )
    expect(screen.getByTestId('tool-group-card')).toHaveTextContent(
      '1 pending',
    )

    // Resolve the second.
    useAppStore.getState().addToolCallMessage(chatId, {
      tool_call_id: 'tc-b',
      name: 'github__search_repositories',
      result: '[]',
      is_error: false,
    })
    const done = useAppStore.getState()
    rerender(
      <MessageList
        messages={done.messagesByChat[chatId] ?? []}
        inflightTools={done.inflightToolsByChat[chatId] ?? []}
      />,
    )
    expect(screen.getByTestId('tool-group-card')).toHaveTextContent(
      'complete',
    )
  })
})
