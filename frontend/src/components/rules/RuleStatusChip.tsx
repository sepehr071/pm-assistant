import clsx from 'clsx'
import type { RuleFiringStatus } from '../../types/rules'
import { statusBadgeStyle, statusLabel } from './ruleStatusStyle'

export interface RuleStatusChipProps {
  status: RuleFiringStatus | null | undefined
  onClick?: () => void
  title?: string
  className?: string
  'data-testid'?: string
}

export function RuleStatusChip({
  status,
  onClick,
  title,
  className,
  ...rest
}: RuleStatusChipProps) {
  const classes = clsx(
    'inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide',
    statusBadgeStyle(status),
    onClick && 'cursor-pointer hover:brightness-125 transition',
    className,
  )
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        title={title}
        className={classes}
        data-testid={rest['data-testid']}
      >
        {statusLabel(status)}
      </button>
    )
  }
  return (
    <span className={classes} title={title} data-testid={rest['data-testid']}>
      {statusLabel(status)}
    </span>
  )
}

export default RuleStatusChip
