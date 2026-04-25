import type { ReactElement } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RuleActivity from '../pages/RuleActivity'
import type {
  FiringTimelineRow,
  Rule,
  RuleDigest,
} from '../types/rules'

function renderWithProviders(ui: ReactElement, initialPath = '/rules/7/activity') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/rules/:id/activity" element={ui} />
          <Route
            path="/chat/:id"
            element={<div data-testid="chat-page">Chat</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

const originalFetch = globalThis.fetch

function makeRule(overrides: Partial<Rule> = {}): Rule {
  return {
    id: 7,
    description: 'Notify me when PRs need review',
    interval_seconds: 300,
    auto_approve: false,
    enabled: true,
    compiled_spec: {
      source_tool: 'github__list_review_requests',
      source_args: {},
      filter: { kind: 'new_pr_review_request' },
      action_prompt: 'DM me',
    },
    activity_conversation_id: 55,
    ...overrides,
  }
}

function makeDigest(overrides: Partial<RuleDigest> = {}): RuleDigest {
  return {
    window: '24h',
    from: new Date(Date.now() - 86_400_000).toISOString(),
    to: new Date().toISOString(),
    matched: 2,
    no_match: 10,
    error: 1,
    pending_approval: 0,
    expired: 0,
    rejected: 0,
    sparkline: Array.from({ length: 24 }, (_, i) => (i % 5 === 0 ? 1 : 0)),
    ...overrides,
  }
}

function makeFiring(
  id: number,
  status: FiringTimelineRow['status'],
  overrides: Partial<FiringTimelineRow> = {},
): FiringTimelineRow {
  return {
    id,
    rule_id: 7,
    fired_at: new Date(Date.now() - id * 60_000).toISOString(),
    status,
    conversation_id: 55,
    trigger_summary:
      status === 'matched'
        ? `matched via github__list_review_requests #${id}`
        : status === 'error'
          ? `error: timeout #${id}`
          : `polled github__list_review_requests`,
    duration_ms: 42,
    ...overrides,
  }
}

interface FetchCfg {
  rules?: Rule[]
  digest?: RuleDigest
  firings?: FiringTimelineRow[]
  messagesByChat?: Record<number, unknown[]>
}

function installFetchMock(cfg: FetchCfg) {
  globalThis.fetch = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/rules' && method === 'GET') {
        return jsonResponse(cfg.rules ?? [])
      }
      if (url.match(/\/api\/rules\/\d+\/digest/) && method === 'GET') {
        return jsonResponse(cfg.digest ?? makeDigest())
      }
      if (url.match(/\/api\/rules\/\d+\/firings/) && method === 'GET') {
        return jsonResponse(cfg.firings ?? [])
      }
      const msgMatch = url.match(/\/api\/chats\/(\d+)\/messages/)
      if (msgMatch && method === 'GET') {
        const id = Number(msgMatch[1])
        return jsonResponse(cfg.messagesByChat?.[id] ?? [])
      }
      return jsonResponse([])
    },
  ) as unknown as typeof fetch
}

describe('RuleActivity page', () => {
  beforeEach(() => {
    // quiet logs
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('renders mixed-status chips in the timeline', async () => {
    const rule = makeRule()
    const firings = [
      makeFiring(1, 'matched'),
      makeFiring(2, 'error'),
      makeFiring(3, 'pending_approval'),
    ]
    installFetchMock({ rules: [rule], firings })

    renderWithProviders(<RuleActivity />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-timeline')).toBeInTheDocument(),
    )
    expect(screen.getByTestId('firing-row-1')).toHaveTextContent(/matched/i)
    expect(screen.getByTestId('firing-row-2')).toHaveTextContent(/error/i)
    expect(screen.getByTestId('firing-row-3')).toHaveTextContent(/pending/i)
  })

  it('auto-groups consecutive no-match rows and expands to show timestamps', async () => {
    const rule = makeRule()
    const firings = [
      makeFiring(10, 'matched'),
      makeFiring(11, 'no_match'),
      makeFiring(12, 'no_match'),
      makeFiring(13, 'no_match'),
      makeFiring(14, 'error'),
    ]
    installFetchMock({ rules: [rule], firings })

    renderWithProviders(<RuleActivity />)

    await waitFor(() =>
      expect(screen.getByTestId('firing-no-match-group')).toBeInTheDocument(),
    )
    const group = screen.getByTestId('firing-no-match-group')
    expect(group).toHaveTextContent(/3 checks, 0 matches/i)
    // Individual rows are hidden until expanded.
    expect(
      screen.queryByTestId('firing-no-match-group-list'),
    ).not.toBeInTheDocument()

    await userEvent.click(within(group).getByRole('button'))
    expect(
      screen.getByTestId('firing-no-match-group-list'),
    ).toBeInTheDocument()
  })

  it('links "Open full chat" to /chat/:activity_conversation_id', async () => {
    const rule = makeRule({ activity_conversation_id: 123 })
    installFetchMock({ rules: [rule], firings: [] })

    renderWithProviders(<RuleActivity />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-activity-open-chat')).toBeInTheDocument(),
    )

    await userEvent.click(screen.getByTestId('rule-activity-open-chat'))

    await waitFor(() =>
      expect(screen.getByTestId('chat-page')).toBeInTheDocument(),
    )
  })

  it('renders a ToolCallCard inside an expanded matched row', async () => {
    const rule = makeRule({ activity_conversation_id: 55 })
    const firedAt = new Date().toISOString()
    const firings = [
      makeFiring(20, 'matched', {
        fired_at: firedAt,
        match_summary: 'got something',
      }),
    ]
    const messages = [
      {
        id: 501,
        chat_id: 55,
        role: 'assistant',
        content: 'running tool',
        tool_calls: [
          {
            id: 'call_999',
            type: 'function',
            function: {
              name: 'github__get_pr',
              arguments: JSON.stringify({ pr: 42 }),
            },
          },
        ],
        created_at: firedAt,
      },
      {
        id: 502,
        chat_id: 55,
        role: 'tool',
        content: '{"ok": true}',
        tool_call_id: 'call_999',
        name: 'github__get_pr',
        created_at: firedAt,
      },
    ]

    installFetchMock({
      rules: [rule],
      firings,
      messagesByChat: { 55: messages },
    })

    renderWithProviders(<RuleActivity />)

    await waitFor(() =>
      expect(screen.getByTestId('firing-row-20')).toBeInTheDocument(),
    )

    await userEvent.click(
      within(screen.getByTestId('firing-row-20')).getByRole('button'),
    )

    await waitFor(() => {
      const card = screen.getByTestId('tool-call-card')
      expect(card).toHaveTextContent('github__get_pr')
    })
  })
})
