import clsx from 'clsx'

export interface ConsecutiveFailureBannerProps {
  count: number
  autoDisabled: boolean
  onReEnable?: () => void
  onDismiss?: () => void
  reEnablePending?: boolean
  'data-testid'?: string
}

export function ConsecutiveFailureBanner({
  count,
  autoDisabled,
  onReEnable,
  onDismiss,
  reEnablePending,
  ...rest
}: ConsecutiveFailureBannerProps) {
  return (
    <div
      data-testid={rest['data-testid'] ?? 'consecutive-failure-banner'}
      className={clsx(
        'glass-panel rounded-xl border border-red-500/40 bg-red-950/30 px-3 py-2',
        'flex flex-wrap items-center gap-2 text-xs text-red-100',
      )}
    >
      <span
        aria-hidden
        className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-red-500/30 text-red-100"
      >
        !
      </span>
      <span className="min-w-0 flex-1">
        {autoDisabled ? (
          <>
            <strong className="text-red-50">Auto-disabled</strong> after {count}{' '}
            consecutive failures. Fix the upstream issue then re-enable.
          </>
        ) : (
          <>
            <strong className="text-red-50">Heads up:</strong> {count}{' '}
            consecutive failures. Rule will auto-disable at 5.
          </>
        )}
      </span>
      {autoDisabled && onReEnable && (
        <button
          type="button"
          onClick={onReEnable}
          disabled={reEnablePending}
          data-testid="consecutive-failure-reenable"
          className="rounded-md bg-gradient-to-br from-emerald-500 to-emerald-600 px-2.5 py-1 text-[11px] font-medium text-white shadow-lg shadow-emerald-500/25 ring-1 ring-emerald-300/20 hover:from-emerald-400 hover:to-emerald-500 transition disabled:opacity-50"
        >
          {reEnablePending ? 'Re-enabling…' : 'Re-enable'}
        </button>
      )}
      {!autoDisabled && onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          data-testid="consecutive-failure-dismiss"
          className="rounded-md border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-neutral-200 hover:bg-white/10 transition"
        >
          Dismiss
        </button>
      )}
    </div>
  )
}

export default ConsecutiveFailureBanner
