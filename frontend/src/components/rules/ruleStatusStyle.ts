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

export function statusLabel(
  status: RuleFiringStatus | null | undefined,
): string {
  switch (status) {
    case 'matched':
      return 'matched'
    case 'pending_approval':
      return 'pending'
    case 'error':
      return 'error'
    case 'expired':
      return 'expired'
    case 'rejected':
      return 'rejected'
    case 'skipped':
      return 'skipped'
    case 'no_match':
      return 'no match'
    default:
      return 'idle'
  }
}
