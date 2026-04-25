import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { getRuleFirings, runRuleNow, updateRule } from '../../lib/api'
import { intervalLabel, type RuleHealth } from '../../types/rules'
import { RuleStatusChip } from './RuleStatusChip'
import { ConsecutiveFailureBanner } from './ConsecutiveFailureBanner'

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'never'
  const diff = Date.now() - then
  const s = Math.floor(diff / 1000)
  if (s < 0) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}

function formatCountdown(
  lastRunIso: string | null | undefined,
  intervalSeconds: number,
  enabled: boolean,
): string {
  if (!enabled) return 'paused'
  if (!lastRunIso) return `in ≤${intervalLabel(intervalSeconds)}`
  const last = new Date(lastRunIso).getTime()
  if (Number.isNaN(last)) return '—'
  const nextMs = last + intervalSeconds * 1000
  const remain = Math.max(0, Math.floor((nextMs - Date.now()) / 1000))
  if (remain <= 0) return 'any moment'
  if (remain < 60) return `${remain}s`
  const m = Math.floor(remain / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h`
}

/**
 * Render a 24-cell sparkline using Tailwind opacity variants. Each cell is
 * tinted proportional to its bucket value vs the max; pure CSS — no lib.
 */
function Sparkline({ values }: { values: number[] }) {
  const normalized = values.length === 24
    ? values
    : values.slice(-24).concat(new Array(Math.max(0, 24 - values.length)).fill(0))
  const max = Math.max(1, ...normalized)
  return (
    <div
      data-testid="rule-sparkline"
      className="flex h-8 items-end gap-[2px]"
      aria-label="24 hour activity"
    >
      {normalized.map((v, i) => {
        const ratio = v === 0 ? 0 : 0.25 + (v / max) * 0.6
        return (
          <span
            key={i}
            data-testid="rule-sparkline-cell"
            className={clsx(
              'inline-block h-full w-2 rounded-sm',
              v === 0 ? 'bg-white/5' : 'bg-emerald-500',
            )}
            style={v === 0 ? undefined : { opacity: ratio }}
            title={`${v} activity`}
          />
        )
      })}
    </div>
  )
}

export interface RuleHealthCardProps {
  health: RuleHealth
}

export function RuleHealthCard({ health }: RuleHealthCardProps) {
  const { rule, digest_24h: digest, consecutive_failures: fails } = health
  const autoDisabled =
    !!health.auto_disabled || (!rule.enabled && fails >= 5)
  const qc = useQueryClient()
  const [dismissed, setDismissed] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['rule-health'] })
    qc.invalidateQueries({ queryKey: ['rules'] })
    qc.invalidateQueries({ queryKey: ['rule-firings-card', rule.id] })
  }

  const toggleMut = useMutation({
    mutationFn: (enabled: boolean) => updateRule(rule.id, { enabled }),
    onSuccess: invalidate,
  })

  const runNowMut = useMutation({
    mutationFn: () => runRuleNow(rule.id),
    onSuccess: () => {
      setRunError(null)
      invalidate()
    },
    onError: (err: unknown) => {
      setRunError(err instanceof Error ? err.message : String(err ?? 'error'))
    },
  })

  // Small secondary fetch — backend doesn't inline recent firings on the
  // health endpoint, so grab the last 5 lazily.
  const { data: recentFirings } = useQuery({
    queryKey: ['rule-firings-card', rule.id],
    queryFn: () => getRuleFirings(rule.id, { limit: 5 }),
    refetchInterval: 30_000,
  })

  const showBanner = fails >= 3 && !(dismissed && !autoDisabled)

  return (
    <div
      data-testid={`rule-health-card-${rule.id}`}
      className="glass-panel rounded-2xl p-4 space-y-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <div
              className="min-w-0 flex-1 text-sm leading-snug text-neutral-100 line-clamp-2 break-words"
              title={rule.description}
            >
              {rule.description}
            </div>
            {rule.auto_approve && (
              <span className="shrink-0 rounded-full border border-red-400/30 bg-red-500/20 px-2 py-0.5 text-[10px] uppercase tracking-wide text-red-200">
                auto-approve
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <button
            type="button"
            role="switch"
            aria-checked={rule.enabled}
            aria-label={rule.enabled ? 'Disable rule' : 'Enable rule'}
            data-testid={`rule-toggle-${rule.id}`}
            disabled={toggleMut.isPending}
            onClick={() => toggleMut.mutate(!rule.enabled)}
            className={clsx(
              'relative inline-flex h-7 w-12 shrink-0 items-center rounded-full border transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-emerald-400/50 focus:ring-offset-2 focus:ring-offset-transparent disabled:cursor-not-allowed disabled:opacity-50',
              rule.enabled
                ? 'border-emerald-400/40 bg-gradient-to-br from-emerald-500 to-emerald-600 shadow-md shadow-emerald-500/30'
                : 'border-white/10 bg-white/10',
            )}
          >
            <span
              className={clsx(
                'inline-block h-5 w-5 transform rounded-full bg-white shadow-lg ring-1 ring-black/10 transition-transform duration-200',
                rule.enabled ? 'translate-x-6' : 'translate-x-1',
              )}
            />
          </button>
          <span
            className={clsx(
              'text-[10px] uppercase tracking-wide font-medium',
              rule.enabled ? 'text-emerald-300' : 'text-neutral-500',
            )}
          >
            {rule.enabled ? 'Enabled' : 'Disabled'}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-neutral-500">
        <span>Every {intervalLabel(rule.interval_seconds)}</span>
        <span>·</span>
        <span>Last run {formatRelative(rule.last_run_at)}</span>
        <span>·</span>
        <span>
          Next run in{' '}
          {formatCountdown(rule.last_run_at, rule.interval_seconds, rule.enabled)}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="rounded-full border border-emerald-400/30 bg-emerald-500/15 px-2 py-0.5 text-emerald-200">
          {digest.matched} matched
        </span>
        <span className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-neutral-300">
          {digest.no_match} no-match
        </span>
        <span className="rounded-full border border-red-400/30 bg-red-500/15 px-2 py-0.5 text-red-200">
          {digest.error} errors
        </span>
      </div>

      <Sparkline values={digest.sparkline ?? []} />

      {recentFirings && recentFirings.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {recentFirings.map((f) => (
            <Link
              key={f.id}
              to={`/rules/${rule.id}/activity#firing-${f.id}`}
              data-testid={`rule-recent-firing-${f.id}`}
              title={f.trigger_summary ?? f.match_summary ?? ''}
            >
              <RuleStatusChip status={f.status} />
            </Link>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 pt-2">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => runNowMut.mutate()}
            disabled={runNowMut.isPending}
            data-testid={`rule-run-now-${rule.id}`}
            className="rounded-lg border border-white/10 bg-white/5 px-3 py-1 text-xs text-neutral-200 backdrop-blur hover:bg-white/10 transition disabled:opacity-50"
          >
            {runNowMut.isPending ? 'Running…' : 'Run now'}
          </button>
          <button
            type="button"
            onClick={() => toggleMut.mutate(!rule.enabled)}
            disabled={toggleMut.isPending}
            data-testid={`rule-pause-${rule.id}`}
            className="rounded-lg border border-white/10 bg-white/5 px-3 py-1 text-xs text-neutral-200 backdrop-blur hover:bg-white/10 transition disabled:opacity-50"
          >
            {rule.enabled ? 'Pause' : 'Resume'}
          </button>
        </div>
        <Link
          to={`/rules/${rule.id}/activity`}
          data-testid={`rule-view-activity-${rule.id}`}
          className="rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-3 py-1 text-xs font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition"
        >
          View activity
        </Link>
      </div>

      {runError && (
        <div
          className="text-xs text-red-400"
          data-testid={`rule-run-error-${rule.id}`}
        >
          {runError}
        </div>
      )}

      {showBanner && (
        <ConsecutiveFailureBanner
          data-testid={`rule-failure-banner-${rule.id}`}
          count={fails}
          autoDisabled={autoDisabled}
          onReEnable={
            autoDisabled ? () => toggleMut.mutate(true) : undefined
          }
          onDismiss={!autoDisabled ? () => setDismissed(true) : undefined}
          reEnablePending={toggleMut.isPending}
        />
      )}
    </div>
  )
}

export default RuleHealthCard
