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
  mentions: string[]
}

export interface RuleFilterNewPrReviewRequest {
  kind: 'new_pr_review_request'
  reviewer?: string
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
}

export interface UpdateRuleBody {
  description?: string
  compiled_spec?: CompiledSpec
  interval_seconds?: number
  auto_approve?: boolean
  enabled?: boolean
}

export interface IntervalOption {
  label: string
  seconds: number
}

export const INTERVAL_OPTIONS: IntervalOption[] = [
  { label: '1 minute', seconds: 60 },
  { label: '5 minutes', seconds: 300 },
  { label: '15 minutes', seconds: 900 },
  { label: '1 hour', seconds: 3600 },
  { label: '6 hours', seconds: 21600 },
  { label: '24 hours', seconds: 86400 },
]

export function intervalLabel(seconds: number): string {
  const match = INTERVAL_OPTIONS.find((o) => o.seconds === seconds)
  if (match) return match.label
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`
  return `${Math.round(seconds / 86400)}d`
}
