import { memo, useState } from 'react'
import clsx from 'clsx'
import { brandFor } from './brands'

export interface ToolCallCardProps {
  toolName: string
  arguments?: Record<string, unknown> | string
  result?: string
  isError?: boolean
  status?: 'pending' | 'success' | 'error'
  defaultOpen?: boolean
  /**
   * When true, renders a compact one-line pill suitable for non-technical
   * users — brand tile + tool name + status, no args/result panel and no
   * expand affordance. Driven by the `show_tool_details` user setting.
   */
  compact?: boolean
}

function formatArgs(
  args: Record<string, unknown> | string | undefined,
): string {
  if (args === undefined) return ''
  if (typeof args === 'string') {
    try {
      return JSON.stringify(JSON.parse(args), null, 2)
    } catch {
      return args
    }
  }
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

function formatResult(result: string | undefined): string {
  if (!result) return ''
  try {
    return JSON.stringify(JSON.parse(result), null, 2)
  } catch {
    return result
  }
}

function integrationOf(toolName: string): string {
  const idx = toolName.indexOf('__')
  return idx === -1 ? '' : toolName.slice(0, idx)
}

function shortToolName(toolName: string): string {
  const idx = toolName.indexOf('__')
  return idx === -1 ? toolName : toolName.slice(idx + 2)
}

function statusLabel(status: 'pending' | 'success' | 'error'): string {
  switch (status) {
    case 'pending':
      return 'running'
    case 'success':
      return 'done'
    case 'error':
      return 'error'
  }
}

function StatusDot({ status }: { status: 'pending' | 'success' | 'error' }) {
  // Smooth color transition on status change so a card going from pending
  // to success/error animates instead of snap-replacing.
  return (
    <span
      aria-hidden
      className={clsx(
        'inline-block h-2 w-2 rounded-full transition-colors duration-300',
        status === 'pending' && 'bg-amber-400 animate-pulse shadow-amber-400/40 shadow-md',
        status === 'success' && 'bg-emerald-500 shadow-emerald-500/40 shadow-md',
        status === 'error' && 'bg-rose-500 shadow-rose-500/40 shadow-md',
      )}
    />
  )
}

function BrandBadge({ toolName }: { toolName: string }) {
  const integration = integrationOf(toolName)
  if (!integration) return null
  const b = brandFor(integration)
  return (
    <span
      aria-hidden
      className={clsx(
        'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-gradient-to-br text-[10px] font-bold ring-1',
        b.gradient,
        b.fg,
        b.ring,
      )}
    >
      {b.initial}
    </span>
  )
}

function ToolCallCardImpl({
  toolName,
  arguments: args,
  result,
  isError,
  status,
  defaultOpen = false,
  compact = false,
}: ToolCallCardProps) {
  const [open, setOpen] = useState(defaultOpen)
  const effectiveStatus: 'pending' | 'success' | 'error' =
    status ??
    (result === undefined ? 'pending' : isError ? 'error' : 'success')

  if (compact) {
    return (
      <div
        data-testid="tool-call-card"
        data-compact="true"
        className={clsx(
          'glass-panel my-1 inline-flex max-w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs transition-all duration-300',
          effectiveStatus === 'pending' && 'border border-amber-400/30',
          effectiveStatus === 'error' && 'border border-rose-500/30',
        )}
      >
        <BrandBadge toolName={toolName} />
        <StatusDot status={effectiveStatus} />
        <span className="truncate font-medium text-neutral-200">
          {shortToolName(toolName)}
        </span>
        <span
          className={clsx(
            'text-[11px]',
            effectiveStatus === 'pending' && 'text-amber-300',
            effectiveStatus === 'success' && 'text-emerald-300',
            effectiveStatus === 'error' && 'text-rose-300',
          )}
        >
          · {statusLabel(effectiveStatus)}
        </span>
      </div>
    )
  }

  return (
    <div
      data-testid="tool-call-card"
      className="glass-panel my-2 rounded-xl text-sm overflow-hidden transition-all duration-300"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-white/5 transition"
      >
        <BrandBadge toolName={toolName} />
        <StatusDot status={effectiveStatus} />
        <span className="font-mono text-neutral-200">{toolName}</span>
        <span
          className={clsx(
            'ml-auto text-xs transition-colors duration-300',
            effectiveStatus === 'pending' && 'text-amber-300',
            effectiveStatus === 'success' && 'text-emerald-300',
            effectiveStatus === 'error' && 'text-rose-300',
          )}
        >
          {statusLabel(effectiveStatus)}
        </span>
        <span className="text-neutral-500">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="border-t border-white/5 px-3 py-2">
          {args !== undefined && (
            <div className="mb-2">
              <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
                Arguments
              </div>
              <pre className="glass-pre whitespace-pre-wrap break-all rounded-md p-2 text-xs text-neutral-200">
                {formatArgs(args)}
              </pre>
            </div>
          )}
          {result !== undefined && (
            <div>
              <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
                Result
              </div>
              <pre
                className={clsx(
                  'whitespace-pre-wrap break-all rounded-md p-2 text-xs border',
                  isError
                    ? 'bg-rose-950/40 text-rose-200 border-rose-900/40'
                    : 'glass-pre text-neutral-200',
                )}
              >
                {formatResult(result)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Streaming chat re-renders the assistant turn on every token. Memo
// here so identical {toolName, arguments, result, isError, status}
// props don't repaint the card on each delta.
export const ToolCallCard = memo(ToolCallCardImpl)

export default ToolCallCard
