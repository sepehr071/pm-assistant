import type { RuleFiringStatus } from '../../types/rules'

export function statusBadgeStyle(
  status: RuleFiringStatus | null | undefined,
): string {
  switch (status) {
    case 'matched':
      return 'bg-emerald-500/20 text-emerald-200 border-emerald-400/30'
    case 'pending_approval':
      return 'bg-amber-500/20 text-amber-200 border-amber-400/30'
    case 'error':
      return 'bg-red-500/20 text-red-200 border-red-400/30'
    case 'expired':
      return 'bg-neutral-500/20 text-neutral-300 border-neutral-400/30'
    case 'rejected':
      return 'bg-rose-500/20 text-rose-200 border-rose-400/30'
    case 'skipped':
      return 'bg-sky-500/10 text-sky-200 border-sky-400/20'
    case 'no_match':
      return 'bg-white/5 text-neutral-400 border-white/10'
    default:
      return 'bg-white/5 text-neutral-400 border-white/10'
  }
}

/** Returns an i18n key for a status. Caller resolves via `useT().t(...)`. */
export function statusLabelKey(
  status: RuleFiringStatus | null | undefined,
): string {
  switch (status) {
    case 'matched':
      return 'rules.status.matched'
    case 'pending_approval':
      return 'rules.status.pending'
    case 'error':
      return 'rules.status.error'
    case 'expired':
      return 'rules.status.expired'
    case 'rejected':
      return 'rules.status.rejected'
    case 'skipped':
      return 'rules.status.skipped'
    case 'no_match':
      return 'rules.status.noMatch'
    default:
      return 'rules.status.idle'
  }
}
