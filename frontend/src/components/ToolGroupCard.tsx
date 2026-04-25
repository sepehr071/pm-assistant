import { memo, useState } from 'react'
import clsx from 'clsx'
import type { ToolCall, Message } from '../types'
import { ToolCallCard } from './ToolCallCard'
import { brandFor } from './brands'

export interface ToolGroupCardProps {
  calls: ToolCall[]
  resultsByCallId: Record<string, Message>
  parseArgs(raw: string | undefined): Record<string, unknown> | string
  /** Pin per-call status so pending and error cards survive the merge. */
  inflightStatuses?: Record<string, 'pending' | 'success' | 'error'>
  /** Compact mode hides args/result panels; controlled by `show_tool_details`. */
  compact?: boolean
}

function integrationOf(toolName: string): string {
  const idx = toolName.indexOf('__')
  return idx === -1 ? toolName : toolName.slice(0, idx)
}

function summarize(calls: ToolCall[]): string {
  const integrations = Array.from(
    new Set(calls.map((c) => integrationOf(c.function.name))),
  )
  if (integrations.length === 1) return integrations[0]
  if (integrations.length <= 3) return integrations.join(' · ')
  return `${integrations.slice(0, 2).join(' · ')} +${integrations.length - 2}`
}

function ToolGroupCardImpl({
  calls,
  resultsByCallId,
  parseArgs,
  inflightStatuses,
  compact = false,
}: ToolGroupCardProps) {
  const [open, setOpen] = useState(false)

  const total = calls.length
  const completed = calls.filter((c) => {
    if (resultsByCallId[c.id] !== undefined) return true
    return inflightStatuses?.[c.id] === 'success'
  }).length
  const pending = total - completed
  const groupStatus: 'pending' | 'success' = pending > 0 ? 'pending' : 'success'

  const dotClass = clsx(
    'inline-block h-2 w-2 rounded-full transition-colors duration-300',
    groupStatus === 'pending' && 'bg-amber-400 animate-pulse',
    groupStatus === 'success' && 'bg-emerald-500',
  )

  const summary = summarize(calls)
  const integrationKey = integrationOf(calls[0]?.function.name ?? '')
  const brand = integrationKey ? brandFor(integrationKey) : null

  if (compact) {
    return (
      <div
        data-testid="tool-group-card"
        data-compact="true"
        className={clsx(
          'glass-panel my-1 inline-flex max-w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs transition-all duration-300',
          groupStatus === 'pending' && 'border border-amber-400/30',
        )}
      >
        {brand && (
          <span
            aria-hidden
            className={clsx(
              'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-gradient-to-br text-[10px] font-bold ring-1',
              brand.gradient,
              brand.fg,
              brand.ring,
            )}
          >
            {brand.initial}
          </span>
        )}
        <span className={dotClass} aria-hidden />
        <span className="truncate font-medium text-neutral-200">{summary}</span>
        <span className="text-[11px] text-neutral-400">
          · {total} tool{total === 1 ? '' : 's'}
          {pending > 0 ? ` · ${pending} running` : ' · done'}
        </span>
      </div>
    )
  }

  return (
    <div
      data-testid="tool-group-card"
      className="glass-panel my-2 rounded-xl text-sm overflow-hidden"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-white/5 transition"
      >
        <span className={dotClass} aria-hidden />
        <span className="font-mono text-neutral-200">{summary}</span>
        <span className="text-xs text-neutral-500">
          · {total} tool{total === 1 ? '' : 's'}
        </span>
        <span className="ml-auto text-xs text-neutral-500">
          {pending > 0 ? `${pending} pending` : 'complete'}
        </span>
        <span className="text-neutral-500">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="border-t border-white/5 bg-white/[0.015] px-2 py-1">
          {calls.map((tc) => {
            const resultMsg = resultsByCallId[tc.id]
            const status =
              inflightStatuses?.[tc.id] ??
              (resultMsg !== undefined ? 'success' : 'pending')
            return (
              <ToolCallCard
                key={tc.id}
                toolName={tc.function.name}
                arguments={parseArgs(tc.function.arguments)}
                result={resultMsg?.content}
                isError={false}
                status={status}
                compact={compact}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

export const ToolGroupCard = memo(ToolGroupCardImpl)

export default ToolGroupCard
