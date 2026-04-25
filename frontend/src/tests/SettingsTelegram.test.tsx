import type { ReactElement } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Settings from '../pages/Settings'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderWithProviders(ui: ReactElement) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/settings']}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

function jsonResponse(body: unknown, init: ResponseInit = { status: 200 }): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
}

interface MockState {
  settings: Record<string, unknown>
  status: {
    polling: boolean
    bound: boolean
    username: string | null
    error: string | null
  }
  pairResponse?: { code: string; expires_at: string }
  integrations?: unknown[]
  mcpStatus?: unknown[]
}

interface CallLog {
  url: string
  method: string
  body?: unknown
}

function installMockFetch(state: MockState): { calls: CallLog[] } {
  const calls: CallLog[] = []
  globalThis.fetch = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      let parsedBody: unknown
      if (init?.body && typeof init.body === 'string') {
        try {
          parsedBody = JSON.parse(init.body)
        } catch {
          parsedBody = init.body
        }
      }
      calls.push({ url, method, body: parsedBody })

      // /api/settings — GET + PATCH
      if (url === '/api/settings') {
        if (method === 'PATCH') {
          const patch = (parsedBody ?? {}) as Record<string, unknown>
          state.settings = { ...state.settings, ...patch }
          return jsonResponse(state.settings)
        }
        return jsonResponse(state.settings)
      }

      // /api/telegram/status
      if (url === '/api/telegram/status') {
        return jsonResponse(state.status)
      }

      // /api/settings/telegram/pair — POST + DELETE
      if (url === '/api/settings/telegram/pair') {
        if (method === 'DELETE') {
          state.settings = {
            ...state.settings,
            telegram_chat_id: null,
            telegram_paired_username: null,
          }
          return new Response(null, { status: 204 })
        }
        if (method === 'POST') {
          if (!state.pairResponse) {
            state.pairResponse = {
              code: 'ABC234',
              expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
            }
          }
          return jsonResponse(state.pairResponse)
        }
      }

      // /api/integrations
      if (url === '/api/integrations') {
        return jsonResponse({ integrations: state.integrations ?? [] })
      }

      // /api/mcp/status
      if (url === '/api/mcp/status') {
        return jsonResponse(state.mcpStatus ?? [])
      }

      return jsonResponse({ detail: `unhandled ${method} ${url}` }, { status: 500 })
    },
  ) as unknown as typeof fetch

  return { calls }
}

const originalFetch = globalThis.fetch

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Settings — Telegram gateway section', () => {
  beforeEach(() => {
    // Clipboard shim so copy-to-clipboard doesn't crash in jsdom
    if (!navigator.clipboard) {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: vi.fn(async () => {}) },
      })
    }
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('test_enable_toggle_persists_setting', async () => {
    const { calls } = installMockFetch({
      settings: {
        default_model: 'gpt-4o-mini',
        system_prompt: '',
        auto_approve_tools: [],
        yolo_mode: false,
        telegram_enabled: false,
        telegram_chat_id: null,
        telegram_paired_username: null,
      },
      status: {
        polling: false,
        bound: false,
        username: null,
        error: null,
      },
    })

    renderWithProviders(<Settings />)

    const toggle = await screen.findByTestId('telegram-enable-toggle')
    expect(toggle).toHaveTextContent(/enable/i)

    await userEvent.click(toggle)

    await waitFor(() => {
      const patch = calls.find(
        (c) => c.url === '/api/settings' && c.method === 'PATCH',
      )
      expect(patch).toBeDefined()
      expect(patch?.body).toEqual({ telegram_enabled: true })
    })
  })

  it('test_generate_code_shows_countdown', async () => {
    const expiresAt = new Date(Date.now() + 15 * 60 * 1000).toISOString()
    installMockFetch({
      settings: {
        default_model: 'gpt-4o-mini',
        system_prompt: '',
        auto_approve_tools: [],
        yolo_mode: false,
        telegram_enabled: true,
        telegram_chat_id: null,
        telegram_paired_username: null,
      },
      status: {
        polling: true,
        bound: false,
        username: null,
        error: null,
      },
      pairResponse: { code: 'XZ42KM', expires_at: expiresAt },
    })

    renderWithProviders(<Settings />)

    const generate = await screen.findByTestId('telegram-generate')
    await userEvent.click(generate)

    const codePill = await screen.findByTestId('telegram-pair-code')
    expect(codePill).toHaveTextContent('XZ42KM')

    const countdown = await screen.findByTestId('telegram-countdown')
    expect(countdown.textContent).toMatch(/Expires in \d{2}:\d{2}/)

    // Instructional text points at DM flow with /pair <code>
    const instructionMatches = screen.getAllByText((_content, el) => {
      if (!el) return false
      // Match the <code> snippet specifically (tagName check narrows result).
      return el.tagName === 'CODE' && el.textContent?.includes('/pair XZ42KM') === true
    })
    expect(instructionMatches.length).toBeGreaterThan(0)
  })

  it('test_connected_state_shows_username_and_unpair_button', async () => {
    installMockFetch({
      settings: {
        default_model: 'gpt-4o-mini',
        system_prompt: '',
        auto_approve_tools: [],
        yolo_mode: false,
        telegram_enabled: true,
        telegram_chat_id: 987654321,
        telegram_paired_username: 'productlead',
      },
      status: {
        polling: true,
        bound: true,
        username: 'productlead',
        error: null,
      },
    })

    renderWithProviders(<Settings />)

    // Username rendered as @productlead
    await waitFor(() =>
      expect(screen.getByText('@productlead')).toBeInTheDocument(),
    )

    // Unpair button present
    const unpair = screen.getByTestId('telegram-unpair')
    expect(unpair).toHaveTextContent(/unpair/i)
  })
})
