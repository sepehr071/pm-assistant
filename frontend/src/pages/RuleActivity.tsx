import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  getRuleDigest,
  getRuleFirings,
  listRules,
  type GetRuleFiringsParams,
} from '../lib/api'
import { intervalLabel, type RuleFiringStatus } from '../types/rules'
import {
  RuleTimeline,
  type FilterKind,
} from '../components/rules/RuleTimeline'

const FILTER_STATUSES: Record<FilterKind, RuleFiringStatus[] | undefined> = {
  all: undefined,
  matched: ['matched'],
  errors: ['error'],
  pending: ['pending_approval'],
}

const FILTERS: Array<{ key: FilterKind; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'matched', label: 'Matched only' },
  { key: 'errors', label: 'Errors only' },
  { key: 'pending', label: 'Pending' },
]

export default function RuleActivity() {
  const { id: idParam } = useParams<{ id: string }>()
  const ruleId = Number(idParam)
  const [filter, setFilter] = useState<FilterKind>('all')

  const { data: rules } = useQuery({
    queryKey: ['rules'],
    queryFn: listRules,
  })
  const rule = useMemo(
    () => (rules ?? []).find((r) => r.id === ruleId) ?? null,
    [rules, ruleId],
  )

  const firingsParams: GetRuleFiringsParams = useMemo(() => {
    const p: GetRuleFiringsParams = { limit: 50 }
    const statuses = FILTER_STATUSES[filter]
    if (statuses && statuses.length > 0) p.status = statuses
    return p
  }, [filter])

  const {
    data: firings,
    isLoading: firingsLoading,
    isError: firingsError,
    error: firingsErrorObj,
  } = useQuery({
    queryKey: ['rule-firings', ruleId, filter],
    queryFn: () => getRuleFirings(ruleId, firingsParams),
    enabled: Number.isFinite(ruleId),
    refetchInterval: 15_000,
  })

  const { data: digest } = useQuery({
    queryKey: ['rule-digest', ruleId],
    queryFn: () => getRuleDigest(ruleId, '24h'),
    enabled: Number.isFinite(ruleId),
    refetchInterval: 30_000,
  })

  if (!Number.isFinite(ruleId)) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl p-8">
          <div className="glass-panel rounded-2xl p-6 text-sm text-red-300">
            Invalid rule id.
          </div>
        </div>
      </div>
    )
  }

  const totalPolls = digest
    ? digest.matched +
      digest.no_match +
      digest.error +
      (digest.pending_approval ?? 0) +
      (digest.expired ?? 0) +
      (digest.rejected ?? 0) +
      (digest.skipped ?? 0)
    : null

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-4 p-8">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-xs uppercase tracking-wider text-neutral-500">
              Rule activity
            </div>
            <h1
              className="mt-1 truncate text-lg font-semibold text-neutral-100"
              title={rule?.description}
              data-testid="rule-activity-title"
            >
              {rule?.description ?? `Rule #${ruleId}`}
            </h1>
            {rule && (
              <div className="mt-1 text-xs text-neutral-500">
                Every {intervalLabel(rule.interval_seconds)}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Link
              to="/rules"
              className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-neutral-200 backdrop-blur hover:bg-white/10 transition"
            >
              ← All rules
            </Link>
            {rule?.activity_conversation_id != null && (
              <Link
                to={`/chat/${rule.activity_conversation_id}`}
                data-testid="rule-activity-open-chat"
                className="rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition"
              >
                Open full chat
              </Link>
            )}
          </div>
        </div>

        {digest && (
          <div
            data-testid="rule-activity-digest"
            className="glass-panel rounded-2xl p-3 text-xs text-neutral-300"
          >
            <span className="uppercase tracking-wider text-neutral-500">
              Last 24h:
            </span>{' '}
            <strong className="text-neutral-100">{totalPolls ?? 0}</strong> polls ·{' '}
            <strong className="text-emerald-200">{digest.matched}</strong>{' '}
            matched ·{' '}
            <strong
              className={digest.error > 0 ? 'text-red-200' : 'text-neutral-200'}
            >
              {digest.error}
            </strong>{' '}
            errors
            {digest.last_error_text && (
              <span className="ml-2 text-neutral-500" title={digest.last_error_text}>
                · last error: {digest.last_error_text.slice(0, 80)}
              </span>
            )}
          </div>
        )}

        <div
          data-testid="rule-activity-filters"
          className="flex flex-wrap gap-1.5"
        >
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              data-testid={`rule-activity-filter-${f.key}`}
              data-active={filter === f.key}
              className={clsx(
                'glass-input rounded-full px-3 py-1 text-xs',
                filter === f.key
                  ? 'border-blue-400/50 text-blue-100'
                  : 'text-neutral-300 hover:text-neutral-100',
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        {firingsLoading && (
          <div
            className="space-y-2"
            data-testid="rule-activity-loading"
            aria-busy="true"
          >
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="glass-panel h-10 animate-pulse rounded-xl"
              />
            ))}
          </div>
        )}

        {firingsError && (
          <div className="glass-panel rounded-2xl p-4 text-sm text-red-300">
            Failed to load firings: {(firingsErrorObj as Error).message}
          </div>
        )}

        {!firingsLoading && !firingsError && (
          <RuleTimeline
            firings={firings ?? []}
            filter={filter}
            conversationId={rule?.activity_conversation_id ?? null}
          />
        )}
      </div>
    </div>
  )
}
