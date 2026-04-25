import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import Integrations from '../components/Integrations'
import { brandFor } from '../components/brands'
import { getMcpStatus, getSettings, patchSettings } from '../lib/api'
import type { Settings as AppSettings, ServerStatus } from '../types'
import { useAppStore } from '../store'

// ---------------------------------------------------------------------------
// Telegram gateway — local types for fields exposed on /api/settings by Lane 3
// (backend/api/telegram.py). Kept local so this file doesn't depend on
// lib/api.ts edits happening in parallel (Lane 2).
// ---------------------------------------------------------------------------

interface TelegramSettings {
  telegram_enabled?: boolean
  telegram_chat_id?: number | null
  telegram_paired_username?: string | null
}

interface TelegramStatus {
  polling: boolean
  bound: boolean
  username: string | null
  error: string | null
}

interface TelegramPairResponse {
  code: string
  expires_at: string // ISO datetime
}

async function fetchTelegramStatus(): Promise<TelegramStatus> {
  const res = await fetch('/api/telegram/status')
  if (!res.ok) {
    throw new Error(`status ${res.status}`)
  }
  return (await res.json()) as TelegramStatus
}

async function postTelegramPair(): Promise<TelegramPairResponse> {
  const res = await fetch('/api/settings/telegram/pair', { method: 'POST' })
  if (!res.ok) {
    throw new Error(`pair failed: ${res.status}`)
  }
  return (await res.json()) as TelegramPairResponse
}

async function deleteTelegramPair(): Promise<void> {
  const res = await fetch('/api/settings/telegram/pair', { method: 'DELETE' })
  if (!res.ok && res.status !== 204) {
    throw new Error(`unpair failed: ${res.status}`)
  }
}

async function patchTelegramEnabled(enabled: boolean): Promise<void> {
  const res = await fetch('/api/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ telegram_enabled: enabled }),
  })
  if (!res.ok) {
    throw new Error(`settings patch failed: ${res.status}`)
  }
}

function formatCountdown(msRemaining: number): string {
  if (msRemaining <= 0) return 'Expired'
  const totalSeconds = Math.floor(msRemaining / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes.toString().padStart(2, '0')}:${seconds
    .toString()
    .padStart(2, '0')}`
}

export default function Settings() {
  const qc = useQueryClient()
  const setStoreSettings = useAppStore((s) => s.setSettings)

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  })

  const [model, setModel] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [dirty, setDirty] = useState(false)
  const [yoloPending, setYoloPending] = useState(false)

  useEffect(() => {
    if (settings) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- form state seeded from server data on initial load and after explicit save; the cascading render is intentional and bounded.
      setModel(settings.default_model ?? '')
      setSystemPrompt(settings.system_prompt ?? '')
      setStoreSettings(settings)
      setDirty(false)
    }
  }, [settings, setStoreSettings])

  const saveMut = useMutation({
    mutationFn: (patch: Partial<AppSettings>) => patchSettings(patch),
    onSuccess: (s) => {
      setStoreSettings(s)
      qc.setQueryData(['settings'], s)
      setDirty(false)
    },
  })

  const yoloOn = settings?.yolo_mode ?? false
  const showToolDetails = settings?.show_tool_details ?? false
  const [toolDetailsPending, setToolDetailsPending] = useState(false)

  const handleToggleYolo = () => {
    if (yoloPending) return
    const next = !yoloOn
    if (next) {
      const ok = window.confirm(
        'YOLO mode will auto-approve EVERY tool call — including writes, deletes, and sends. Are you sure?'
      )
      if (!ok) return
    }
    setYoloPending(true)
    saveMut.mutate(
      { yolo_mode: next } as Partial<AppSettings>,
      { onSettled: () => setYoloPending(false) }
    )
  }

  const handleToggleToolDetails = () => {
    if (toolDetailsPending) return
    setToolDetailsPending(true)
    saveMut.mutate(
      { show_tool_details: !showToolDetails } as Partial<AppSettings>,
      { onSettled: () => setToolDetailsPending(false) }
    )
  }

  const mcp = useQuery({
    queryKey: ['mcp-status'],
    queryFn: getMcpStatus,
    refetchInterval: 15_000,
  })

  const handleSave = () => {
    saveMut.mutate({
      default_model: model,
      system_prompt: systemPrompt,
    } as Partial<AppSettings>)
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl p-8 space-y-10">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100">Settings</h1>
          <p className="text-sm text-neutral-500">
            Configure the assistant and inspect MCP servers.
          </p>
        </div>

        <section className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            General
          </h2>

          {isLoading && (
            <div className="text-sm text-neutral-500">Loading…</div>
          )}

          {!isLoading && (
            <div className="glass-panel space-y-4 rounded-2xl p-5">
              <div>
                <label className="mb-1 block text-sm text-neutral-300">
                  Default model
                </label>
                <input
                  type="text"
                  value={model}
                  onChange={(e) => {
                    setModel(e.target.value)
                    setDirty(true)
                  }}
                  placeholder="gpt-4o-mini"
                  className="glass-input w-full rounded-lg px-3 py-2 text-sm text-neutral-100 outline-none"
                />
              </div>

              <div>
                <label className="mb-1 block text-sm text-neutral-300">
                  Additional instructions
                </label>
                <textarea
                  value={systemPrompt}
                  onChange={(e) => {
                    setSystemPrompt(e.target.value)
                    setDirty(true)
                  }}
                  rows={6}
                  placeholder="Optional. Appended to PM Assistant's built-in role + capabilities prompt."
                  className="glass-input w-full rounded-lg px-3 py-2 text-sm text-neutral-100 outline-none font-mono"
                />
                <p className="mt-1 text-xs text-neutral-500">
                  PM Assistant always knows it's an assistant with Jira / GitHub /
                  Slack / Notion tools and that writes need approval. Use this
                  field to tweak its persona, output style, or domain context.
                </p>
              </div>

              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={!dirty || saveMut.isPending}
                  className="rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saveMut.isPending ? 'Saving…' : 'Save'}
                </button>
                {saveMut.isError && (
                  <span className="text-xs text-red-400">
                    {(saveMut.error as Error).message}
                  </span>
                )}
                {!dirty && !saveMut.isPending && settings && (
                  <span className="text-xs text-neutral-500">Saved</span>
                )}
              </div>
            </div>
          )}
        </section>

        <section className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            Auto-approve
          </h2>

          <div
            className={clsx(
              'rounded-2xl p-5',
              yoloOn
                ? 'border border-red-500/40 bg-red-950/30 backdrop-blur-md shadow-lg shadow-red-500/10'
                : 'glass-panel'
            )}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-neutral-100">
                    YOLO mode
                  </span>
                  <span
                    className={clsx(
                      'rounded-full px-2 py-0.5 text-xs font-medium border backdrop-blur',
                      yoloOn
                        ? 'bg-red-500/20 text-red-200 border-red-500/40'
                        : 'bg-white/5 text-neutral-400 border-white/10'
                    )}
                  >
                    {yoloOn ? 'ON' : 'OFF'}
                  </span>
                </div>
                <p className="mt-1 text-xs text-neutral-400">
                  Auto-approve <span className="font-semibold text-neutral-200">every</span>{' '}
                  tool call — including writes, deletes, and sends. No approval
                  popups will appear.
                </p>
              </div>
              <button
                type="button"
                onClick={handleToggleYolo}
                disabled={yoloPending}
                className={clsx(
                  'shrink-0 rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
                  yoloOn
                    ? 'bg-gradient-to-br from-red-500 to-red-600 text-white shadow-lg shadow-red-500/30 ring-1 ring-red-300/20 hover:from-red-400 hover:to-red-500'
                    : 'glass-input text-neutral-200 hover:bg-white/10'
                )}
              >
                {yoloPending ? 'Saving…' : yoloOn ? 'Disable YOLO' : 'Enable YOLO'}
              </button>
            </div>
          </div>
        </section>

        <section className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            Display
          </h2>

          <div className="glass-panel rounded-2xl p-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-neutral-100">
                    Show tool call details
                  </span>
                  <span
                    className={clsx(
                      'rounded-full px-2 py-0.5 text-xs font-medium border backdrop-blur',
                      showToolDetails
                        ? 'bg-emerald-500/20 text-emerald-200 border-emerald-400/30'
                        : 'bg-white/5 text-neutral-400 border-white/10'
                    )}
                  >
                    {showToolDetails ? 'ON' : 'OFF'}
                  </span>
                </div>
                <p className="mt-1 text-xs text-neutral-400">
                  When off, tool calls collapse to a one-line pill in chat.
                  When on, the full request arguments and raw JSON response
                  are shown — useful for debugging, noisy for everyday use.
                </p>
              </div>
              <button
                type="button"
                onClick={handleToggleToolDetails}
                disabled={toolDetailsPending}
                className={clsx(
                  'shrink-0 rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
                  showToolDetails
                    ? 'glass-input text-neutral-200 hover:bg-white/10'
                    : 'bg-gradient-to-br from-blue-500 to-blue-600 text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500'
                )}
              >
                {toolDetailsPending
                  ? 'Saving…'
                  : showToolDetails
                    ? 'Hide details'
                    : 'Show details'}
              </button>
            </div>
          </div>
        </section>

        <Integrations />

        <TelegramGatewaySection />

        <McpStatusSection
          data={mcp.data}
          isLoading={mcp.isLoading}
          isError={mcp.isError}
          error={mcp.error as Error | null}
          onRefresh={() => mcp.refetch()}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// McpStatusSection — collapsed accordion rows. Compact summary, expand for detail.
// ---------------------------------------------------------------------------

interface McpStatusSectionProps {
  data: ServerStatus[] | undefined
  isLoading: boolean
  isError: boolean
  error: Error | null
  onRefresh: () => void
}

function McpStatusSection({
  data,
  isLoading,
  isError,
  error,
  onRefresh,
}: McpStatusSectionProps) {
  const [openName, setOpenName] = useState<string | null>(null)

  const counts = useMemo(() => {
    if (!data) return { total: 0, healthy: 0, unhealthy: 0 }
    const total = data.length
    const healthy = data.filter((s) => s.healthy).length
    return { total, healthy, unhealthy: total - healthy }
  }, [data])

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            MCP Status
          </h2>
          {data && counts.total > 0 && (
            <p className="text-xs text-neutral-500">
              {counts.healthy} healthy · {counts.total} total
              {counts.unhealthy ? ` · ${counts.unhealthy} error` : ''}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="text-xs text-neutral-400 hover:text-neutral-200"
        >
          Refresh
        </button>
      </div>

      {isLoading && (
        <div className="text-sm text-neutral-500">Loading servers…</div>
      )}

      {isError && (
        <div className="text-sm text-red-400">
          Failed to load status: {error?.message}
        </div>
      )}

      {data && data.length === 0 && (
        <div className="glass-panel rounded-2xl p-5 text-sm text-neutral-500">
          No MCP servers configured.
        </div>
      )}

      {data && data.length > 0 && (
        <>
          <div className="flex flex-wrap gap-2">
            {data.map((server) => {
              const isOpen = openName === server.name
              const healthy = server.healthy
              const b = brandFor(server.name)
              return (
                <button
                  type="button"
                  key={server.name}
                  onClick={() => setOpenName(isOpen ? null : server.name)}
                  aria-pressed={isOpen}
                  className={clsx(
                    'glass-panel inline-flex items-center gap-2 rounded-xl px-2.5 py-1.5 transition',
                    'hover:-translate-y-0.5 hover:shadow-lg',
                    isOpen && 'ring-2',
                    isOpen && b.ring,
                    !healthy && 'border border-red-500/30',
                  )}
                  title={server.error ?? undefined}
                >
                  <span
                    className={clsx(
                      'inline-flex h-6 w-6 items-center justify-center rounded-md bg-gradient-to-br text-xs font-bold ring-1',
                      b.gradient,
                      b.fg,
                      b.ring,
                    )}
                    aria-hidden
                  >
                    {b.initial}
                  </span>
                  <span className="text-sm font-medium text-neutral-100">
                    {server.name}
                  </span>
                  <span
                    aria-hidden
                    className={clsx(
                      'inline-block h-1.5 w-1.5 rounded-full shadow-md shrink-0',
                      healthy
                        ? 'bg-emerald-400 shadow-emerald-400/50'
                        : 'bg-red-400 shadow-red-400/50',
                    )}
                  />
                </button>
              )
            })}
          </div>
          {(() => {
            const sel = data.find((s) => s.name === openName)
            if (!sel) return null
            return (
              <div className="glass-elevated rounded-2xl p-4 transition">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-neutral-100">
                      {sel.name}
                    </div>
                    <div className="mt-0.5 text-xs text-neutral-500">
                      {sel.tool_count} tool{sel.tool_count === 1 ? '' : 's'} ·{' '}
                      <span className={sel.healthy ? 'text-emerald-300' : 'text-red-300'}>
                        {sel.healthy ? 'healthy' : 'error'}
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setOpenName(null)}
                    aria-label="Close"
                    className="text-neutral-500 hover:text-neutral-200"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="h-4 w-4"
                      aria-hidden
                    >
                      <path d="M18 6L6 18M6 6l12 12" />
                    </svg>
                  </button>
                </div>
                {sel.error && (
                  <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200 break-all">
                    {sel.error}
                  </div>
                )}
              </div>
            )
          })()}
        </>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// TelegramGatewaySection
// ---------------------------------------------------------------------------

interface PairState {
  code: string
  expiresAt: number // epoch ms
}

function TelegramGatewaySection() {
  const qc = useQueryClient()
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  })

  const tg = settings as (AppSettings & TelegramSettings) | undefined
  const enabled = tg?.telegram_enabled ?? false
  const bound = (tg?.telegram_chat_id ?? null) !== null
  const pairedUsername = tg?.telegram_paired_username ?? null

  const status = useQuery({
    queryKey: ['telegram-status'],
    queryFn: fetchTelegramStatus,
    refetchInterval: 5_000,
  })

  const [pair, setPair] = useState<PairState | null>(null)
  const [now, setNow] = useState<number>(() => Date.now())
  const [copied, setCopied] = useState(false)

  // Tick every second while a pairing code is live so the countdown updates.
  useEffect(() => {
    if (!pair) return
    const id = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(id)
  }, [pair])

  const msRemaining = pair ? pair.expiresAt - now : 0
  const expired = pair !== null && msRemaining <= 0

  const enableMut = useMutation({
    mutationFn: (next: boolean) => patchTelegramEnabled(next),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['settings'] })
      await qc.invalidateQueries({ queryKey: ['telegram-status'] })
    },
  })

  const pairMut = useMutation({
    mutationFn: postTelegramPair,
    onSuccess: (data) => {
      setPair({ code: data.code, expiresAt: Date.parse(data.expires_at) })
      setCopied(false)
    },
  })

  const unpairMut = useMutation({
    mutationFn: deleteTelegramPair,
    onSuccess: async () => {
      setPair(null)
      await qc.invalidateQueries({ queryKey: ['settings'] })
      await qc.invalidateQueries({ queryKey: ['telegram-status'] })
    },
  })

  const handleCopy = async () => {
    if (!pair) return
    try {
      await navigator.clipboard.writeText(pair.code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1_500)
    } catch {
      // clipboard may be unavailable (e.g. non-secure context); silent fallback.
    }
  }

  const statusDot = useMemo(() => {
    if (!enabled || !status.data) {
      return {
        color: 'bg-neutral-500',
        glow: 'shadow-neutral-500/20',
        label: 'Disabled',
      }
    }
    if (status.data.error) {
      return {
        color: 'bg-red-500',
        glow: 'shadow-red-500/40',
        label: status.data.error,
      }
    }
    if (status.data.polling) {
      return {
        color: 'bg-emerald-500',
        glow: 'shadow-emerald-500/40',
        label: 'Polling Telegram',
      }
    }
    return {
      color: 'bg-amber-500',
      glow: 'shadow-amber-500/40',
      label: 'Bot token not set',
    }
  }, [enabled, status.data])

  return (
    <section className="space-y-4" data-testid="telegram-section">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
        Telegram gateway
      </h2>

      <div className="glass-panel space-y-5 rounded-2xl p-5">
        {/* Toggle + status dot row */}
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold text-neutral-100">
                Enable Telegram gateway
              </span>
              <span
                className={clsx(
                  'rounded-full px-2 py-0.5 text-xs font-medium border backdrop-blur',
                  enabled
                    ? 'bg-emerald-500/20 text-emerald-200 border-emerald-400/30'
                    : 'bg-white/5 text-neutral-400 border-white/10',
                )}
              >
                {enabled ? 'ON' : 'OFF'}
              </span>
            </div>
            <p className="mt-1 text-xs text-neutral-400">
              Chat with the assistant from Telegram. Approvals, streaming
              replies, and tool calls flow through the same plumbing as the web
              UI.
            </p>
            <div
              className="mt-2 flex items-center gap-2"
              data-testid="telegram-status"
            >
              <span
                aria-hidden
                className={clsx(
                  'inline-block h-2 w-2 rounded-full shadow-md',
                  statusDot.color,
                  statusDot.glow,
                )}
              />
              <span className="text-xs text-neutral-400">
                {statusDot.label}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => enableMut.mutate(!enabled)}
            disabled={enableMut.isPending}
            data-testid="telegram-enable-toggle"
            className={clsx(
              'shrink-0 rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
              enabled
                ? 'glass-input text-neutral-200 hover:bg-white/10'
                : 'bg-gradient-to-br from-emerald-500 to-blue-600 text-white shadow-lg shadow-emerald-500/25 ring-1 ring-emerald-300/20 hover:from-emerald-400 hover:to-blue-500',
            )}
          >
            {enableMut.isPending
              ? 'Saving…'
              : enabled
                ? 'Disable'
                : 'Enable'}
          </button>
        </div>

        {/* Body — pairing / bound states — dimmed when not enabled */}
        <div
          className={clsx(
            'rounded-xl transition',
            !enabled && 'pointer-events-none opacity-50',
          )}
          aria-disabled={!enabled}
          data-testid="telegram-body"
        >
          {bound ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="text-sm text-neutral-200">
                  Connected as{' '}
                  <span className="font-semibold text-neutral-100">
                    @{pairedUsername ?? 'unknown'}
                  </span>
                </div>
                <p className="mt-1 text-xs text-neutral-500">
                  DMs from this account will open a Telegram chat in the
                  assistant.
                </p>
              </div>
              <button
                type="button"
                onClick={() => unpairMut.mutate()}
                disabled={unpairMut.isPending}
                data-testid="telegram-unpair"
                className="glass-input shrink-0 rounded-lg px-4 py-2 text-sm font-medium text-neutral-200 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {unpairMut.isPending ? 'Unpairing…' : 'Unpair'}
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {pair && !expired ? (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <span
                      data-testid="telegram-pair-code"
                      className="glass-pre inline-block rounded-xl px-4 py-2 font-mono text-2xl tracking-widest text-neutral-100"
                    >
                      {pair.code}
                    </span>
                    <button
                      type="button"
                      onClick={handleCopy}
                      data-testid="telegram-copy"
                      aria-label="Copy pairing code"
                      title={copied ? 'Copied' : 'Copy to clipboard'}
                      className="glass-input inline-flex h-9 w-9 items-center justify-center rounded-lg text-neutral-200 hover:bg-white/10"
                    >
                      {copied ? (
                        <svg
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="h-4 w-4"
                          aria-hidden
                        >
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                      ) : (
                        <svg
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="h-4 w-4"
                          aria-hidden
                        >
                          <rect x="9" y="9" width="13" height="13" rx="2" />
                          <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                        </svg>
                      )}
                    </button>
                    <span
                      data-testid="telegram-countdown"
                      className="text-xs text-neutral-400"
                    >
                      Expires in {formatCountdown(msRemaining)}
                    </span>
                  </div>
                  <p className="text-xs text-neutral-500">
                    DM your bot{' '}
                    <code className="glass-pre rounded px-1 py-0.5 font-mono text-xs text-neutral-200">
                      /pair {pair.code}
                    </code>{' '}
                    from Telegram.
                  </p>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => pairMut.mutate()}
                  disabled={pairMut.isPending || !enabled}
                  data-testid="telegram-generate"
                  className="rounded-lg bg-gradient-to-br from-emerald-500 to-blue-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-emerald-500/25 ring-1 ring-emerald-300/20 hover:from-emerald-400 hover:to-blue-500 transition disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {pairMut.isPending
                    ? 'Generating…'
                    : expired
                      ? 'Regenerate'
                      : 'Generate pairing code'}
                </button>
              )}

              {pairMut.isError && (
                <div className="text-xs text-red-400">
                  {(pairMut.error as Error).message}
                </div>
              )}

              {unpairMut.isError && (
                <div className="text-xs text-red-400">
                  {(unpairMut.error as Error).message}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
