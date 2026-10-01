import { useEffect } from 'react'
import clsx from 'clsx'
import { useAppStore } from '../store'
import { useT } from '../i18n/useT'

const AUTO_DISMISS_MS = 6_000

/**
 * Top-right transient notification stack. Reads the `toasts` slice from the
 * Zustand store; each toast auto-dismisses after ~6s and can be closed
 * manually. Rendered once near the app root so any code path can surface a
 * message via `useAppStore.getState().pushToast(...)` — including events that
 * fire while a different chat is on screen.
 */
export function Toaster() {
  const { t } = useT()
  const toasts = useAppStore((s) => s.toasts)
  const dismissToast = useAppStore((s) => s.dismissToast)

  // One timer per live toast. Re-runs whenever the set of ids changes; the
  // dependency is the joined id list so re-renders that don't add/remove a
  // toast don't reset pending timers.
  const ids = toasts.map((tst) => tst.id).join(',')
  useEffect(() => {
    if (toasts.length === 0) return
    const timers = toasts.map((tst) =>
      window.setTimeout(() => dismissToast(tst.id), AUTO_DISMISS_MS),
    )
    return () => {
      for (const id of timers) window.clearTimeout(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, dismissToast])

  if (toasts.length === 0) return null

  return (
    <div
      data-testid="toaster"
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed end-4 top-4 z-50 flex w-full max-w-sm flex-col gap-2"
    >
      {toasts.map((tst) => (
        <div
          key={tst.id}
          data-testid="toast"
          data-kind={tst.kind}
          className={clsx(
            'glass-elevated pointer-events-auto flex items-start gap-3 rounded-xl px-4 py-3 text-sm shadow-lg',
            tst.kind === 'error'
              ? 'ring-1 ring-rose-400/30 text-rose-100'
              : 'ring-1 ring-blue-400/30 text-neutral-100',
          )}
        >
          <span
            aria-hidden
            className={clsx(
              'mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full',
              tst.kind === 'error'
                ? 'bg-rose-400 shadow-md shadow-rose-400/40'
                : 'bg-blue-400 shadow-md shadow-blue-400/40',
            )}
          />
          <span dir="auto" className="min-w-0 flex-1 break-words">
            {tst.text}
          </span>
          <button
            type="button"
            data-testid="toast-dismiss"
            aria-label={t('common.dismiss')}
            onClick={() => dismissToast(tst.id)}
            className="shrink-0 rounded p-0.5 text-neutral-400 transition hover:text-neutral-100 hover:bg-white/10"
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
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  )
}

export default Toaster
