// Types for the Proactive Rules feature. The backend is not yet
// implemented — these mirror the `compiled_spec` contract defined in the
// design doc under `backend/services/rule_compiler.py`.

export type RuleFilterKind =
  | 'message_from_user_contains'
  | 'new_issue_mentions'
  | 'new_pr_review_request'
  | 'schedule_only'

export interface RuleFilterMessageFromUserContains {
  kind: 'message_from_user_contains'
  user: string
  contains: string[]
}

export interface RuleFilterNewIssueMentions {
  kind: 'new_issue_mentions'
  mention: string
  repo?: string | null
}

export interface RuleFilterNewPrReviewRequest {
  kind: 'new_pr_review_request'
  mention: string
  repo?: string | null
}

export interface RuleFilterScheduleOnly {
  kind: 'schedule_only'
}

export type RuleFilter =
  | RuleFilterMessageFromUserContains
  | RuleFilterNewIssueMentions
  | RuleFilterNewPrReviewRequest
  | RuleFilterScheduleOnly

export interface CompiledSpec {
  source_tool: string
  source_args: Record<string, unknown>
  filter: RuleFilter
  action_prompt: string
}

export type RuleFiringStatus =
  | 'matched'
  | 'no_match'
  | 'error'
  | 'pending_approval'
  | 'expired'
  | 'rejected'
  | 'skipped'

export interface RuleFiring {
  id: number
  rule_id: number
  fired_at: string
  status: RuleFiringStatus
  match_summary?: string | null
  message_id?: number | null
  error?: string | null
  /** Human-readable explanation of why a tick did not match (or errored).
   * Backend contract: `GET /api/rules/{id}/firings` rows carry this for
   * `no_match` / `error` firings. */
  diagnostic?: string | null
}

export interface Rule {
  id: number
  description: string
  compiled_spec: CompiledSpec
  interval_seconds: number
  auto_approve: boolean
  enabled: boolean
  state_cursor?: string | null
  last_run_at?: string | null
  last_error?: string | null
  last_firing_status?: RuleFiringStatus | null
  recent_firings?: RuleFiring[]
  activity_conversation_id?: number | null
  created_at?: string
  updated_at?: string
}

// Backend response from `GET /api/rules/{id}/firings` — superset of
// `RuleFiring` with denormalized fields used by the timeline UI.
export interface FiringTimelineRow extends RuleFiring {
  conversation_id?: number | null
  trigger_summary?: string | null
  duration_ms?: number | null
}

// Backend response from `GET /api/rules/{id}/digest?window=24h`.
export interface RuleDigest {
  window: string
  from: string
  to: string
  matched: number
  no_match: number
  error: number
  pending_approval: number
  expired: number
  rejected: number
  skipped?: number
  first_match_at?: string | null
  last_error_at?: string | null
  last_error_text?: string | null
  /** 24 hourly buckets, most recent last, counting matched+error. */
  sparkline: number[]
}

// Backend response from `GET /api/rules/health` — one row per rule.
export interface RuleHealth {
  rule: Rule
  digest_24h: RuleDigest
  consecutive_failures: number
  auto_disabled?: boolean
}

export interface CreateRuleBody {
  description: string
  interval_seconds: number
  auto_approve: boolean
  compiled_spec?: CompiledSpec
}

export interface UpdateRuleBody {
  description?: string
  compiled_spec?: CompiledSpec
  interval_seconds?: number
  auto_approve?: boolean
  enabled?: boolean
}

export type IntervalOptionKey =
  | 'rules.interval.option.1m'
  | 'rules.interval.option.5m'
  | 'rules.interval.option.15m'
  | 'rules.interval.option.1h'
  | 'rules.interval.option.6h'
  | 'rules.interval.option.24h'

export interface IntervalOption {
  /** i18n key resolved at render time. */
  labelKey: IntervalOptionKey
  seconds: number
}

export const INTERVAL_OPTIONS: IntervalOption[] = [
  { labelKey: 'rules.interval.option.1m', seconds: 60 },
  { labelKey: 'rules.interval.option.5m', seconds: 300 },
  { labelKey: 'rules.interval.option.15m', seconds: 900 },
  { labelKey: 'rules.interval.option.1h', seconds: 3600 },
  { labelKey: 'rules.interval.option.6h', seconds: 21600 },
  { labelKey: 'rules.interval.option.24h', seconds: 86400 },
]

/** Narrow translator surface this function actually uses, declared so the
 * full `TKey` (which is wider) is assignable. Avoids importing `TKey` here
 * to keep `types/` free of i18n deps. */
export type IntervalTranslator = (
  key:
    | 'rules.interval.option.1m'
    | 'rules.interval.option.5m'
    | 'rules.interval.option.15m'
    | 'rules.interval.option.1h'
    | 'rules.interval.option.6h'
    | 'rules.interval.option.24h'
    | 'rules.interval.fmt.seconds'
    | 'rules.interval.fmt.minutes'
    | 'rules.interval.fmt.hours'
    | 'rules.interval.fmt.days',
  vars?: Record<string, string | number>,
) => string

/**
 * Translate an interval (in seconds) to a localised short label using a
 * caller-provided translator. Kept as a plain function — components import
 * `useT` themselves and pass `t` so this module stays free of React deps.
 */
export function intervalLabel(
  seconds: number,
  t: IntervalTranslator,
): string {
  const match = INTERVAL_OPTIONS.find((o) => o.seconds === seconds)
  if (match) return t(match.labelKey)
  if (seconds < 60) return t('rules.interval.fmt.seconds', { n: seconds })
  if (seconds < 3600)
    return t('rules.interval.fmt.minutes', { n: Math.round(seconds / 60) })
  if (seconds < 86400)
    return t('rules.interval.fmt.hours', { n: Math.round(seconds / 3600) })
  return t('rules.interval.fmt.days', { n: Math.round(seconds / 86400) })
}
