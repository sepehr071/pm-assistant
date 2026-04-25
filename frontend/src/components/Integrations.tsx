import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  connectIntegration,
  disconnectIntegration,
  listIntegrations,
  refreshIntegration,
} from '../lib/api'
import type { Integration, IntegrationState } from '../types'
import { brandFor } from './brands'

type StateStyle = { label: string; tone: string; dot: string }

const STATE_STYLES: Record<IntegrationState, StateStyle> = {
  connected: {
    label: 'connected',
    tone: 'bg-emerald-500/15 text-emerald-200 border border-emerald-400/30',
    dot: 'bg-emerald-400 shadow-emerald-400/50',
  },
  auth_required: {
    label: 'sign-in',
    tone: 'bg-amber-500/15 text-amber-200 border border-amber-400/30',
    dot: 'bg-amber-400 shadow-amber-400/50',
  },
  input_required: {
    label: 'config',
    tone: 'bg-amber-500/15 text-amber-200 border border-amber-400/30',
    dot: 'bg-amber-400 shadow-amber-400/50',
  },
  error: {
    label: 'error',
    tone: 'bg-red-500/15 text-red-200 border border-red-400/30',
    dot: 'bg-red-400 shadow-red-400/50',
  },
  disconnected: {
    label: 'idle',
    tone: 'bg-white/5 text-neutral-300 border border-white/10',
    dot: 'bg-neutral-500 shadow-neutral-500/30',
  },
  unconfigured: {
    label: 'setup',
    tone: 'bg-white/5 text-neutral-400 border border-white/10',
    dot: 'bg-neutral-600 shadow-neutral-600/30',
  },
}

function StatePill({ state }: { state: IntegrationState }) {
  const style = STATE_STYLES[state] ?? STATE_STYLES.disconnected
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium backdrop-blur',
        style.tone,
      )}
    >
      <span
        aria-hidden
        className={clsx('inline-block h-1.5 w-1.5 rounded-full shadow-md', style.dot)}
      />
      {style.label}
    </span>
  )
}

function BrandTile({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' }) {
  const b = brandFor(name)
  const dims = size === 'sm' ? 'h-8 w-8 text-sm' : 'h-10 w-10 text-base'
  return (
    <span
      className={clsx(
        'inline-flex shrink-0 items-center justify-center rounded-xl bg-gradient-to-br font-bold ring-1 shadow-lg',
        b.gradient,
        b.fg,
        b.ring,
        dims,
      )}
      aria-hidden
    >
      {b.initial}
    </span>
  )
}

function openSetupPopup(url: string): Window | null {
  const w = 600
  const h = 720
  const left = window.screenX + (window.outerWidth - w) / 2
  const top = window.screenY + (window.outerHeight - h) / 2
  const popup = window.open(
    url,
    'smithery-oauth',
    `popup=yes,width=${w},height=${h},left=${left},top=${top},noopener,noreferrer`,
  )
  // With `noopener` the returned handle is null. Defensively null
  // `.opener` if browser still hands one back so the OAuth popup can
  // never reverse-tabnab the parent.
  if (popup) {
    try {
      popup.opener = null
    } catch {
      // cross-origin frame — already isolated.
    }
  }
  return popup
}

export default function Integrations() {
  const qc = useQueryClient()
  const { data: integrations = [], isLoading, isError, error } = useQuery({
    queryKey: ['integrations'],
    queryFn: listIntegrations,
    refetchInterval: 30_000,
  })

  const [pollingName, setPollingName] = useState<string | null>(null)
  const popupRef = useRef<Window | null>(null)
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  const stopPolling = () => {
    if (pollTimer.current !== null) {
      clearInterval(pollTimer.current)
      pollTimer.current = null
    }
    setPollingName(null)
    popupRef.current = null
  }

  useEffect(() => () => stopPolling(), [])

  const connectMut = useMutation({
    mutationFn: (name: string) => connectIntegration(name),
    onSuccess: (next) => {
      qc.setQueryData<Integration[]>(['integrations'], (prev) =>
        prev ? prev.map((i) => (i.name === next.name ? next : i)) : prev,
      )
      if (next.state === 'auth_required' && next.setup_url) {
        const popup = openSetupPopup(next.setup_url)
        popupRef.current = popup
        setPollingName(next.name)
        pollTimer.current = setInterval(async () => {
          const popupClosed = !popup || popup.closed
          try {
            const fresh = await refreshIntegration(next.name)
            qc.setQueryData<Integration[]>(['integrations'], (prev) =>
              prev
                ? prev.map((i) => (i.name === fresh.name ? fresh : i))
                : prev,
            )
            if (fresh.state === 'connected' || fresh.state === 'error') {
              if (popup && !popup.closed) popup.close()
              stopPolling()
              qc.invalidateQueries({ queryKey: ['mcp-status'] })
              qc.invalidateQueries({ queryKey: ['tools'] })
            } else if (popupClosed) {
              stopPolling()
            }
          } catch {
            if (popupClosed) stopPolling()
          }
        }, 2000)
      } else if (next.state === 'connected') {
        qc.invalidateQueries({ queryKey: ['mcp-status'] })
      }
    },
  })

  const disconnectMut = useMutation({
    mutationFn: (name: string) => disconnectIntegration(name),
    onSuccess: (next) => {
      qc.setQueryData<Integration[]>(['integrations'], (prev) =>
        prev ? prev.map((i) => (i.name === next.name ? next : i)) : prev,
      )
      qc.invalidateQueries({ queryKey: ['mcp-status'] })
    },
  })

  const refreshMut = useMutation({
    mutationFn: (name: string) => refreshIntegration(name),
    onSuccess: (next) => {
      qc.setQueryData<Integration[]>(['integrations'], (prev) =>
        prev ? prev.map((i) => (i.name === next.name ? next : i)) : prev,
      )
    },
  })

  const counts = useMemo(() => {
    const total = integrations.length
    const connected = integrations.filter((i) => i.state === 'connected').length
    const needsAttention = integrations.filter(
      (i) => i.state === 'auth_required' || i.state === 'input_required' || i.state === 'error',
    ).length
    return { total, connected, needsAttention }
  }, [integrations])

  const selectedIntegration = useMemo(
    () => integrations.find((i) => i.name === selected) ?? null,
    [integrations, selected],
  )

  const renderActions = (i: Integration) => {
    const isBusy =
      (connectMut.isPending && connectMut.variables === i.name) ||
      (disconnectMut.isPending && disconnectMut.variables === i.name) ||
      (refreshMut.isPending && refreshMut.variables === i.name) ||
      pollingName === i.name

    if (i.state === 'unconfigured') {
      return (
        <span className="text-xs text-neutral-500">
          Set <code className="text-neutral-300">SMITHERY_API_KEY</code> and
          restart the backend.
        </span>
      )
    }

    if (i.state === 'connected') {
      return (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => refreshMut.mutate(i.name)}
            disabled={isBusy}
            className="glass-input rounded-lg px-3 py-1.5 text-xs text-neutral-200 hover:bg-white/10 transition disabled:opacity-50"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={() => disconnectMut.mutate(i.name)}
            disabled={isBusy}
            className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-200 backdrop-blur hover:bg-red-500/20 transition disabled:opacity-50"
          >
            Disconnect
          </button>
        </div>
      )
    }

    const ctaLabel = pollingName === i.name ? 'Waiting…' : 'Connect'

    return (
      <button
        type="button"
        onClick={() => connectMut.mutate(i.name)}
        disabled={isBusy}
        className="rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition disabled:cursor-not-allowed disabled:opacity-50"
      >
        {ctaLabel}
      </button>
    )
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
            Integrations
          </h2>
          <p className="text-xs text-neutral-500">
            {counts.total > 0
              ? `${counts.connected} connected · ${counts.total} total${counts.needsAttention ? ` · ${counts.needsAttention} need attention` : ''}`
              : 'Each integration runs through Smithery.'}
          </p>
        </div>
        <button
          type="button"
          onClick={() =>
            qc.invalidateQueries({ queryKey: ['integrations'] })
          }
          className="text-xs text-neutral-400 hover:text-neutral-200"
        >
          Refresh all
        </button>
      </div>

      {isLoading && (
        <div className="text-sm text-neutral-500">Loading integrations…</div>
      )}

      {isError && (
        <div className="text-sm text-red-400">
          Failed to load: {(error as Error).message}
        </div>
      )}

      {!isLoading && integrations.length === 0 && (
        <div className="glass-panel rounded-2xl p-5 text-sm text-neutral-500">
          No integrations configured in <code>backend/integrations.json</code>.
        </div>
      )}

      {integrations.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {integrations.map((i) => {
            const isSelected = selected === i.name
            const b = brandFor(i.name)
            return (
              <button
                type="button"
                key={i.name}
                onClick={() => setSelected(isSelected ? null : i.name)}
                aria-pressed={isSelected}
                className={clsx(
                  'glass-panel group relative flex flex-col gap-3 rounded-2xl p-4 text-left transition',
                  'hover:-translate-y-0.5 hover:shadow-xl',
                  isSelected && 'ring-2 ring-offset-0',
                  isSelected && b.ring,
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <BrandTile name={i.name} />
                  <StatePill state={i.state} />
                </div>
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-neutral-100">
                    {i.label}
                  </div>
                  <div className="mt-0.5 text-[11px] text-neutral-500">
                    {i.tool_count} tool{i.tool_count === 1 ? '' : 's'}
                  </div>
                </div>
                {pollingName === i.name && (
                  <div className="text-[11px] text-amber-300/80">
                    Awaiting sign-in…
                  </div>
                )}
              </button>
            )
          })}
        </div>
      )}

      {selectedIntegration && (
        <div
          className="glass-elevated relative rounded-2xl p-5 transition"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              <BrandTile name={selectedIntegration.name} />
              <div className="min-w-0">
                <div className="text-base font-semibold text-neutral-100">
                  {selectedIntegration.label}
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-xs text-neutral-500">
                  <StatePill state={selectedIntegration.state} />
                  <span>·</span>
                  <span>{selectedIntegration.tool_count} tools</span>
                  {selectedIntegration.server_name && (
                    <>
                      <span>·</span>
                      <span className="truncate font-mono">
                        {selectedIntegration.server_name}
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setSelected(null)}
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

          {selectedIntegration.error && (
            <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200 break-all">
              {selectedIntegration.error}
            </div>
          )}

          <div className="mt-4 flex items-center justify-end">
            {renderActions(selectedIntegration)}
          </div>
        </div>
      )}
    </section>
  )
}
