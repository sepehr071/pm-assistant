import type { ReactElement } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Rules from '../pages/Rules'
import type { RuleHealth } from '../types/rules'
import { useAppStore } from '../store'
import type { Settings as SettingsType } from '../types'

function renderWithProviders(ui: ReactElement, initialPath = '/rules') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/rules" element={ui} />
          <Route
            path="/rules/:id/activity"
            element={<div data-testid="activity-page">Activity</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function jsonResponse(
  body: unknown,
  init: ResponseInit = { status: 200 },
): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
}

const originalFetch = globalThis.fetch

function makeHealth(overrides: Partial<RuleHealth> = {}): RuleHealth {
  const base: RuleHealth = {
    rule: {
      id: 11,
      description: 'dm me about roadmap',
      interval_seconds: 300,
      auto_approve: false,
      enabled: true,
      compiled_spec: {
        source_tool: 'slack__conversations_history',
        source_args: {},
        filter: {
          kind: 'message_from_user_contains',
          user: 'Alice',
          contains: ['roadmap'],
        },
        action_prompt: 'Reply',
      },
      activity_conversation_id: 42,
      last_run_at: new Date(Date.now() - 60_000).toISOString(),
    },
    digest_24h: {
      window: '24h',
      from: new Date(Date.now() - 86_400_000).toISOString(),
      to: new Date().toISOString(),
      matched: 3,
      no_match: 1437,
      error: 0,
      pending_approval: 0,
      expired: 0,
      rejected: 0,
      sparkline: Array.from({ length: 24 }, (_, i) => (i % 6 === 0 ? 1 : 0)),
    },
    consecutive_failures: 0,
  }
  return {
    ...base,
    ...overrides,
    rule: { ...base.rule, ...(overrides.rule ?? {}) },
    digest_24h: { ...base.digest_24h, ...(overrides.digest_24h ?? {}) },
  }
}

interface RecordedCall {
  url: string
  method: string
  body: unknown
}

function installFetchMock(
  healthResponses: RuleHealth[][],
  recorded?: RecordedCall[],
) {
  let i = 0
  globalThis.fetch = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (recorded) {
        let parsedBody: unknown = undefined
        if (typeof init?.body === 'string') {
          try {
            parsedBody = JSON.parse(init.body)
          } catch {
            parsedBody = init.body
          }
        }
        recorded.push({ url, method, body: parsedBody })
      }
      if (url.includes('/api/rules/health') && method === 'GET') {
        const body = healthResponses[Math.min(i, healthResponses.length - 1)]
        i++
        return jsonResponse(body)
      }
      if (url.match(/\/api\/rules\/\d+\/firings/) && method === 'GET') {
        return jsonResponse([])
      }
      if (url === '/api/rules' && method === 'GET') {
        return jsonResponse([])
      }
      if (url === '/api/rules/compile' && method === 'POST') {
        const reqBody =
          typeof init?.body === 'string'
            ? (JSON.parse(init.body) as { description?: string })
            : {}
        return jsonResponse({
          compiled_spec: {
            source_tool: 'slack__conversations_history',
            source_args: {},
            filter: {
              kind: 'message_from_user_contains',
              user: 'Alice',
              contains: ['recompiled'],
            },
            action_prompt: `recompiled from: ${reqBody.description ?? ''}`,
          },
        })
      }
      if (url.match(/\/api\/rules\/\d+$/) && method === 'PATCH') {
        const id = Number(url.split('/').pop())
        return jsonResponse({ ...makeHealth().rule, id })
      }
      return jsonResponse({ detail: `unexpected url: ${url}` }, { status: 500 })
    },
  ) as unknown as typeof fetch
}

describe('Rules dashboard', () => {
  beforeEach(() => {
    vi.spyOn(window, 'confirm').mockImplementation(() => true)
    // Force English UI; tests assert against English copy.
    const baseSettings: SettingsType = {
      system_prompt: '',
      auto_approve_tools: [],
      yolo_mode: false,
      dangerous_always_approve: true,
      show_tool_details: false,
      language: 'en',
    }
    useAppStore.setState({ settings: baseSettings })
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('renders an empty state when no rules exist', async () => {
    installFetchMock([[]])
    renderWithProviders(<Rules />)
    await waitFor(() =>
      expect(screen.getByTestId('rules-empty')).toBeInTheDocument(),
    )
  })

  it('renders one health card per rule with a 24-cell sparkline', async () => {
    const health = [
      makeHealth({ rule: { ...makeHealth().rule, id: 1, description: 'rule one' } }),
      makeHealth({ rule: { ...makeHealth().rule, id: 2, description: 'rule two' } }),
    ]
    installFetchMock([health])
    renderWithProviders(<Rules />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-health-card-1')).toBeInTheDocument(),
    )
    expect(screen.getByTestId('rule-health-card-2')).toBeInTheDocument()
    const card = screen.getByTestId('rule-health-card-1')
    expect(within(card).getByTestId('rule-sparkline')).toBeInTheDocument()
    expect(within(card).getAllByTestId('rule-sparkline-cell')).toHaveLength(24)
    expect(within(card).getByText(/3 matched/)).toBeInTheDocument()
    expect(within(card).getByText(/1437 no-match/)).toBeInTheDocument()
  })

  it('shows the consecutive-failure banner at the warn threshold', async () => {
    const health = [
      makeHealth({
        rule: { ...makeHealth().rule, id: 5, description: 'flaky rule' },
        consecutive_failures: 3,
      }),
    ]
    installFetchMock([health])
    renderWithProviders(<Rules />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-failure-banner-5')).toBeInTheDocument(),
    )
    expect(screen.getByText(/3 consecutive failures/i)).toBeInTheDocument()
    // Warning variant: Dismiss button, no re-enable.
    expect(screen.getByTestId('consecutive-failure-dismiss')).toBeInTheDocument()
  })

  it('shows auto-disabled banner with Re-enable at 5+ failures', async () => {
    const health = [
      makeHealth({
        rule: { ...makeHealth().rule, id: 6, description: 'broken rule', enabled: false },
        consecutive_failures: 5,
        auto_disabled: true,
      }),
    ]
    installFetchMock([health])
    renderWithProviders(<Rules />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-failure-banner-6')).toBeInTheDocument(),
    )
    expect(screen.getByText(/Auto-disabled/i)).toBeInTheDocument()
    expect(screen.getByTestId('consecutive-failure-reenable')).toBeInTheDocument()
  })

  it('navigates to /rules/:id/activity when "View activity" is clicked', async () => {
    const health = [
      makeHealth({ rule: { ...makeHealth().rule, id: 9, description: 'look at me' } }),
    ]
    installFetchMock([health])
    renderWithProviders(<Rules />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-health-card-9')).toBeInTheDocument(),
    )

    await userEvent.click(screen.getByTestId('rule-view-activity-9'))

    await waitFor(() =>
      expect(screen.getByTestId('activity-page')).toBeInTheDocument(),
    )
  })

  it('opens the editor prefilled when Edit is clicked', async () => {
    const health = [
      makeHealth({
        rule: { ...makeHealth().rule, id: 12, description: 'watch the roadmap' },
      }),
    ]
    installFetchMock([health])
    renderWithProviders(<Rules />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-health-card-12')).toBeInTheDocument(),
    )

    await userEvent.click(screen.getByTestId('rule-edit-12'))

    const editor = await screen.findByTestId('rule-editor')
    // Prefilled with the existing description.
    const desc = within(editor).getByTestId(
      'rule-editor-description',
    ) as HTMLTextAreaElement
    expect(desc.value).toBe('watch the roadmap')
    // Editing an existing rule shows the edit title, not "New rule".
    expect(within(editor).getByText(/Edit rule/i)).toBeInTheDocument()
  })

  it('saves an unchanged edit via PATCH without recompiling', async () => {
    const recorded: RecordedCall[] = []
    const health = [
      makeHealth({
        rule: { ...makeHealth().rule, id: 12, description: 'watch the roadmap' },
      }),
    ]
    installFetchMock([health], recorded)
    renderWithProviders(<Rules />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-health-card-12')).toBeInTheDocument(),
    )

    await userEvent.click(screen.getByTestId('rule-edit-12'))
    const editor = await screen.findByTestId('rule-editor')

    // Go straight to review (spec already known) then save.
    await userEvent.click(within(editor).getByTestId('rule-editor-review-step'))
    await userEvent.click(within(editor).getByTestId('rule-editor-save'))

    await waitFor(() => {
      const patch = recorded.find(
        (c) => c.method === 'PATCH' && /\/api\/rules\/12$/.test(c.url),
      )
      expect(patch).toBeTruthy()
    })
    // No recompile happened because the description was untouched.
    expect(
      recorded.find((c) => c.url === '/api/rules/compile'),
    ).toBeFalsy()
    // No POST /api/rules (that is create-only).
    expect(
      recorded.find((c) => c.method === 'POST' && c.url === '/api/rules'),
    ).toBeFalsy()
  })

  it('recompiles before PATCH when the description is edited', async () => {
    const recorded: RecordedCall[] = []
    const health = [
      makeHealth({
        rule: { ...makeHealth().rule, id: 12, description: 'watch the roadmap' },
      }),
    ]
    installFetchMock([health], recorded)
    renderWithProviders(<Rules />)

    await waitFor(() =>
      expect(screen.getByTestId('rule-health-card-12')).toBeInTheDocument(),
    )

    await userEvent.click(screen.getByTestId('rule-edit-12'))
    const editor = await screen.findByTestId('rule-editor')

    const desc = within(editor).getByTestId('rule-editor-description')
    await userEvent.clear(desc)
    await userEvent.type(desc, 'watch the launch plan instead')
    await userEvent.click(within(editor).getByTestId('rule-editor-compile'))

    // After recompile we land on the review step, then save.
    await waitFor(() =>
      expect(
        within(editor).getByTestId('rule-editor-review'),
      ).toBeInTheDocument(),
    )
    await userEvent.click(within(editor).getByTestId('rule-editor-save'))

    await waitFor(() => {
      const patch = recorded.find(
        (c) => c.method === 'PATCH' && /\/api\/rules\/12$/.test(c.url),
      )
      expect(patch).toBeTruthy()
    })
    expect(
      recorded.find((c) => c.url === '/api/rules/compile'),
    ).toBeTruthy()
    const patch = recorded.find(
      (c) => c.method === 'PATCH' && /\/api\/rules\/12$/.test(c.url),
    )
    const body = patch?.body as {
      description?: string
      compiled_spec?: { action_prompt?: string }
    }
    expect(body.description).toBe('watch the launch plan instead')
    // The PATCH carries the freshly recompiled spec.
    expect(body.compiled_spec?.action_prompt).toContain('recompiled from')
  })
})
