import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import Integrations from '../components/Integrations'
import { BrandGlyph } from '../components/brands'
import { brandFor } from '../components/brandRegistry'
import { getMcpStatus, getSettings, patchSettings } from '../lib/api'
import type { Settings as AppSettings, Language, ServerStatus } from '../types'
import { useAppStore } from '../store'
import { useT } from '../i18n/useT'

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
  const { t } = useT()
  const qc = useQueryClient()
  const setStoreSettings = useAppStore((s) => s.setSettings)

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  })

  const [systemPrompt, setSystemPrompt] = useState('')
  const [dirty, setDirty] = useState(false)
  const [yoloPending, setYoloPending] = useState(false)
  const [dangerousPending, setDangerousPending] = useState(false)

  useEffect(() => {
    if (settings) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- form state seeded from server data on initial load and after explicit save; the cascading render is intentional and bounded.
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
  const dangerousOn = settings?.dangerous_always_approve ?? true
  const showToolDetails = settings?.show_tool_details ?? false
  const currentLang: Language = settings?.language ?? 'fa'
  const [toolDetailsPending, setToolDetailsPending] = useState(false)

  const handleToggleYolo = () => {
    if (yoloPending) return
    const next = !yoloOn
    if (next) {
      const ok = window.confirm(t('settings.yoloConfirm'))
      if (!ok) return
    }
    setYoloPending(true)
    saveMut.mutate(
      { yolo_mode: next } as Partial<AppSettings>,
      { onSettled: () => setYoloPending(false) }
    )
  }

  const handleToggleDangerous = () => {
    if (dangerousPending) return
    const next = !dangerousOn
    if (!next) {
      const ok = window.confirm(t('settings.guardOffConfirm'))
      if (!ok) return
    }
    setDangerousPending(true)
    saveMut.mutate(
      { dangerous_always_approve: next } as Partial<AppSettings>,
      { onSettled: () => setDangerousPending(false) },
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

  const handleSetLanguage = (next: Language) => {
    if (currentLang === next) return
    saveMut.mutate({ language: next } as Partial<AppSettings>)
  }

  const mcp = useQuery({
    queryKey: ['mcp-status'],
    queryFn: getMcpStatus,
    refetchInterval: 15_000,
  })

  const handleSave = () => {
    saveMut.mutate({
      system_prompt: systemPrompt,
    } as Partial<AppSettings>)
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl p-8 space-y-10">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100">
            {t('settings.title')}
          </h1>
          <p className="text-sm text-neutral-500">{t('settings.subtitle')}</p>
        </div>

        <section className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            {t('settings.language')}
          </h2>
          <div className="glass-panel rounded-2xl p-5 space-y-3">
            <p className="text-xs text-neutral-400">
              {t('settings.languageHelp')}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => handleSetLanguage('fa')}
                data-active={currentLang === 'fa'}
                className="glass-input rounded-lg px-3 py-1.5 text-sm data-[active=true]:bg-white/10 data-[active=true]:ring-1 data-[active=true]:ring-blue-400/40"
              >
                <span dir="rtl">{t('settings.langPersian')}</span>
              </button>
              <button
                type="button"
                onClick={() => handleSetLanguage('en')}
                data-active={currentLang === 'en'}
                className="glass-input rounded-lg px-3 py-1.5 text-sm data-[active=true]:bg-white/10 data-[active=true]:ring-1 data-[active=true]:ring-blue-400/40"
              >
                <span dir="ltr">{t('settings.langEnglish')}</span>
              </button>
            </div>
          </div>
        </section>

        <section className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            {t('settings.general')}
          </h2>

          {isLoading && (
            <div className="text-sm text-neutral-500">
              {t('common.loading')}
            </div>
          )}

          {!isLoading && (
            <div className="glass-panel space-y-4 rounded-2xl p-5">
              <div>
                <label className="mb-1 block text-sm text-neutral-300">
                  {t('settings.additionalInstructions')}
                </label>
                <textarea
                  value={systemPrompt}
                  onChange={(e) => {
                    setSystemPrompt(e.target.value)
                    setDirty(true)
                  }}
                  rows={6}
                  dir="auto"
                  placeholder={t('settings.additionalInstructionsHelp')}
                  className="glass-input w-full rounded-lg px-3 py-2 text-sm leading-relaxed text-neutral-100 outline-none placeholder:text-neutral-500"
                />
                <p className="mt-1 text-xs text-neutral-500">
                  {t('settings.additionalInstructionsLong')}
                </p>
              </div>

              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={!dirty || saveMut.isPending}
                  className="rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saveMut.isPending ? t('common.saving') : t('common.save')}
                </button>
                {saveMut.isError && (
                  <span className="text-xs text-red-400">
                    {(saveMut.error as Error).message}
                  </span>
                )}
                {!dirty && !saveMut.isPending && settings && (
                  <span className="text-xs text-neutral-500">
                    {t('common.saved')}
                  </span>
                )}
              </div>
            </div>
          )}
        </section>

        <section className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            {t('settings.autoApprove')}
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
                    {t('settings.yoloMode')}
                  </span>
                  <span
                    className={clsx(
                      'rounded-full px-2 py-0.5 text-xs font-medium border backdrop-blur',
                      yoloOn
                        ? 'bg-red-500/20 text-red-200 border-red-500/40'
                        : 'bg-white/5 text-neutral-400 border-white/10'
                    )}
                  >
                    {yoloOn ? t('common.on') : t('common.off')}
                  </span>
                </div>
                <p className="mt-1 text-xs text-neutral-400">
                  {t('settings.yoloDesc')}
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
                {yoloPending
                  ? t('common.saving')
                  : yoloOn
                    ? t('settings.yoloDisable')
                    : t('settings.yoloEnable')}
              </button>
            </div>
          </div>

          <div className="glass-panel rounded-2xl p-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-neutral-100">
                    {t('settings.dangerousGuard')}
                  </span>
                  <span
                    className={clsx(
                      'rounded-full px-2 py-0.5 text-xs font-medium border backdrop-blur',
                      dangerousOn
                        ? 'bg-amber-500/20 text-amber-200 border-amber-400/30'
                        : 'bg-white/5 text-neutral-400 border-white/10',
                    )}
                  >
                    {dangerousOn ? t('common.on') : t('common.off')}
                  </span>
                </div>
                <p className="mt-1 text-xs text-neutral-400">
                  {t('settings.dangerousGuardDesc')}
                </p>
              </div>
              <button
                type="button"
                onClick={handleToggleDangerous}
                disabled={dangerousPending}
                className={clsx(
                  'shrink-0 rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
                  dangerousOn
                    ? 'bg-gradient-to-br from-amber-500 to-amber-600 text-white shadow-lg shadow-amber-500/25 ring-1 ring-amber-300/20 hover:from-amber-400 hover:to-amber-500'
                    : 'glass-input text-neutral-200 hover:bg-white/10',
                )}
              >
                {dangerousPending
                  ? t('common.saving')
                  : dangerousOn
                    ? t('settings.guardDisable')
                    : t('settings.guardEnable')}
              </button>
            </div>
          </div>
        </section>

        <section className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            {t('settings.display')}
          </h2>

          <div className="glass-panel rounded-2xl p-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-neutral-100">
                    {t('settings.showToolDetails')}
                  </span>
                  <span
                    className={clsx(
                      'rounded-full px-2 py-0.5 text-xs font-medium border backdrop-blur',
                      showToolDetails
                        ? 'bg-emerald-500/20 text-emerald-200 border-emerald-400/30'
                        : 'bg-white/5 text-neutral-400 border-white/10'
                    )}
                  >
                    {showToolDetails ? t('common.on') : t('common.off')}
                  </span>
                </div>
                <p className="mt-1 text-xs text-neutral-400">
                  {t('settings.showToolDetailsDesc')}
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
                  ? t('common.saving')
                  : showToolDetails
                    ? t('settings.hideDetails')
                    : t('settings.showDetails')}
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
  const { t } = useT()
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
            {t('settings.mcpStatus')}
          </h2>
          {data && counts.total > 0 && (
            <p className="text-xs text-neutral-500">
              {counts.unhealthy
                ? t('settings.mcpHealthyWithErrors', {
                    healthy: counts.healthy,
                    total: counts.total,
                    errors: counts.unhealthy,
                  })
                : t('settings.mcpHealthy', {
                    healthy: counts.healthy,
                    total: counts.total,
                  })}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="text-xs text-neutral-400 hover:text-neutral-200"
        >
          {t('common.refresh')}
        </button>
      </div>

      {isLoading && (
        <div className="text-sm text-neutral-500">
          {t('settings.mcpLoading')}
        </div>
      )}

      {isError && (
        <div className="text-sm text-red-400">
          {t('settings.mcpFailedToLoad', { error: error?.message ?? '' })}
        </div>
      )}

      {data && data.length === 0 && (
        <div className="glass-panel rounded-2xl p-5 text-sm text-neutral-500">
          {t('settings.mcpEmpty')}
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
                  <BrandGlyph
                    name={server.name}
                    className="h-6 w-6"
                    imgClassName="h-3.5 w-3.5"
                    fontClassName="text-xs"
                  />
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
                      {sel.tool_count === 1
                        ? t('settings.mcpToolsOne')
                        : t('settings.mcpToolsOther', { n: sel.tool_count })}
                      {' · '}
                      <span className={sel.healthy ? 'text-emerald-300' : 'text-red-300'}>
                        {sel.healthy ? t('common.healthy') : t('common.error')}
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setOpenName(null)}
                    aria-label={t('common.close')}
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
  const { t } = useT()
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
        label: t('settings.telegramDisabled'),
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
        label: t('settings.telegramPolling'),
      }
    }
    return {
      color: 'bg-amber-500',
      glow: 'shadow-amber-500/40',
      label: t('settings.telegramTokenMissing'),
    }
  }, [enabled, status.data, t])

  return (
    <section className="space-y-4" data-testid="telegram-section">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
        {t('settings.telegramTitle')}
      </h2>

      <div className="glass-panel space-y-5 rounded-2xl p-5">
        {/* Toggle + status dot row */}
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold text-neutral-100">
                {t('settings.telegramEnable')}
              </span>
              <span
                className={clsx(
                  'rounded-full px-2 py-0.5 text-xs font-medium border backdrop-blur',
                  enabled
                    ? 'bg-emerald-500/20 text-emerald-200 border-emerald-400/30'
                    : 'bg-white/5 text-neutral-400 border-white/10',
                )}
              >
                {enabled ? t('common.on') : t('common.off')}
              </span>
            </div>
            <p className="mt-1 text-xs text-neutral-400">
              {t('settings.telegramDesc')}
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
              ? t('common.saving')
              : enabled
                ? t('settings.telegramDisableBtn')
                : t('settings.telegramEnableBtn')}
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
                  {t('settings.telegramConnectedAs', {
                    username: pairedUsername ?? 'unknown',
                  })
                    .split(/(@\S+)/)
                    .map((chunk, i) =>
                      chunk.startsWith('@') ? (
                        <span
                          key={i}
                          dir="ltr"
                          className="font-semibold text-neutral-100"
                        >
                          {chunk}
                        </span>
                      ) : (
                        <span key={i}>{chunk}</span>
                      ),
                    )}
                </div>
                <p className="mt-1 text-xs text-neutral-500">
                  {t('settings.telegramConnectedDesc')}
                </p>
              </div>
              <button
                type="button"
                onClick={() => unpairMut.mutate()}
                disabled={unpairMut.isPending}
                data-testid="telegram-unpair"
                className="glass-input shrink-0 rounded-lg px-4 py-2 text-sm font-medium text-neutral-200 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {unpairMut.isPending
                  ? t('settings.telegramUnpairing')
                  : t('settings.telegramUnpair')}
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {pair && !expired ? (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <span
                      data-testid="telegram-pair-code"
                      dir="ltr"
                      className="glass-pre inline-block rounded-xl px-4 py-2 font-mono text-2xl tracking-widest text-neutral-100"
                    >
                      {pair.code}
                    </span>
                    <button
                      type="button"
                      onClick={handleCopy}
                      data-testid="telegram-copy"
                      aria-label={t('settings.telegramCopyCode')}
                      title={copied ? t('common.copied') : t('common.copy')}
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
                      {t('settings.telegramExpiresIn', {
                        countdown: formatCountdown(msRemaining),
                      })}
                    </span>
                  </div>
                  <p className="text-xs text-neutral-500">
                    {(() => {
                      const tpl = t('settings.telegramPairInstr', {
                        code: pair.code,
                      })
                      // Wrap the "/pair <code>" segment in dir="ltr" + glass-pre
                      // so the slash command renders Latin-style mid-Persian.
                      const cmd = `/pair ${pair.code}`
                      const idx = tpl.indexOf(cmd)
                      if (idx === -1) return tpl
                      return (
                        <>
                          {tpl.slice(0, idx)}
                          <code
                            dir="ltr"
                            className="glass-pre rounded px-1 py-0.5 font-mono text-xs text-neutral-200"
                          >
                            {cmd}
                          </code>
                          {tpl.slice(idx + cmd.length)}
                        </>
                      )
                    })()}
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
                    ? t('settings.telegramGenerating')
                    : expired
                      ? t('settings.telegramRegenerate')
                      : t('settings.telegramGenerate')}
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
