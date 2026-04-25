import { useState } from 'react'
import clsx from 'clsx'
import type { PendingApproval } from '../types'

export interface ToolApprovalProps {
  pending: PendingApproval
  onApprove(toolCallId: string, alwaysApprove: boolean): void | Promise<void>
  onReject(toolCallId: string): void | Promise<void>
}

function formatToolName(name: string): string {
  const idx = name.indexOf('__')
  if (idx !== -1) {
    return `${name.slice(0, idx)} / ${name.slice(idx + 2)}`
  }
  return name
}

export function ToolApproval({
  pending,
  onApprove,
  onReject,
}: ToolApprovalProps) {
  const [alwaysApprove, setAlwaysApprove] = useState(false)
  const [busy, setBusy] = useState(false)

  const argsText = (() => {
    try {
      return JSON.stringify(pending.arguments ?? {}, null, 2)
    } catch {
      return String(pending.arguments)
    }
  })()

  const approve = async () => {
    if (busy) return
    setBusy(true)
    try {
      await onApprove(pending.tool_call_id, alwaysApprove)
    } finally {
      setBusy(false)
    }
  }

  const reject = async () => {
    if (busy) return
    setBusy(true)
    try {
      await onReject(pending.tool_call_id)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="tool-approval-title"
      data-testid="tool-approval"
      className="glass-backdrop fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="glass-elevated w-full max-w-lg rounded-2xl overflow-hidden">
        <div className="border-b border-white/10 px-5 py-3">
          <h2
            id="tool-approval-title"
            className="text-base font-semibold text-neutral-100"
          >
            Approve tool call
          </h2>
          <p
            data-testid="tool-approval-name"
            className="mt-1 font-mono text-sm text-neutral-300"
          >
            {formatToolName(pending.tool_name)}
          </p>
        </div>
        <div className="px-5 py-4">
          <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
            Arguments
          </div>
          <pre
            data-testid="tool-approval-args"
            className="glass-pre max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md p-3 text-xs text-neutral-200"
          >
            {argsText}
          </pre>
          <label className="mt-3 flex items-center gap-2 text-xs text-neutral-400">
            <input
              type="checkbox"
              checked={alwaysApprove}
              onChange={(e) => setAlwaysApprove(e.target.checked)}
              data-testid="tool-approval-always"
              className="accent-blue-500"
            />
            Always auto-approve
            <span className="font-mono text-neutral-300">
              {pending.tool_name}
            </span>
          </label>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-white/10 px-5 py-3">
          <button
            type="button"
            onClick={() => void reject()}
            disabled={busy}
            data-testid="tool-approval-reject"
            className={clsx(
              'rounded-lg border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-neutral-200 backdrop-blur',
              'hover:bg-white/10 transition disabled:opacity-50',
            )}
          >
            Reject
          </button>
          <button
            type="button"
            onClick={() => void approve()}
            disabled={busy}
            data-testid="tool-approval-approve"
            className={clsx(
              'rounded-lg bg-gradient-to-br from-emerald-500 to-emerald-600 px-4 py-1.5 text-sm font-medium text-white',
              'shadow-lg shadow-emerald-500/30 ring-1 ring-emerald-300/20',
              'hover:from-emerald-400 hover:to-emerald-500 transition disabled:opacity-50',
            )}
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}

export default ToolApproval
