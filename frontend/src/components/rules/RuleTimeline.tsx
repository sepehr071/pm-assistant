import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { getMessages } from '../../lib/api'
import type { FiringTimelineRow } from '../../types/rules'
import type { Message } from '../../types'
import { ToolCallCard } from '../ToolCallCard'
import { ToolGroupCard } from '../ToolGroupCard'
import { RuleStatusChip } from './RuleStatusChip'

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diff = Date.now() - then
  const s = Math.floor(diff / 1000)
  if (s < 0) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}

function formatClock(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function parseToolArgs(
  raw: string | undefined,
): Record<string, unknown> | string {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      return parsed as Record<string, unknown>
    }
    return raw
  } catch {
    return raw
  }
}

type TimelineItem =
  | { kind: 'firing'; firing: FiringTimelineRow }
  | {
      kind: 'no-match-group'
      firings: FiringTimelineRow[]
    }

function buildGroupedItems(
  firings: FiringTimelineRow[],
  filter: FilterKind,
): TimelineItem[] {
  if (filter !== 'all') {
    return firings.map((f) => ({ kind: 'firing' as const, firing: f }))
  }
  const out: TimelineItem[] = []
  let buffer: FiringTimelineRow[] = []
  const flush = () => {
    if (buffer.length === 0) return
    if (buffer.length === 1) {
      out.push({ kind: 'firing', firing: buffer[0] })
    } else {
      out.push({ kind: 'no-match-group', firings: buffer.slice() })
    }
    buffer = []
  }
  for (const f of firings) {
    if (f.status === 'no_match') {
      buffer.push(f)
    } else {
      flush()
      out.push({ kind: 'firing', firing: f })
    }
  }
  flush()
  return out
}

export type FilterKind = 'all' | 'matched' | 'errors' | 'pending'

export interface RuleTimelineProps {
  firings: FiringTimelineRow[]
  filter: FilterKind
  conversationId?: number | null
}

interface ExpandedFiringRowProps {
  firing: FiringTimelineRow
  conversationId?: number | null
}

function ExpandedFiringRow({
  firing,
  conversationId,
}: ExpandedFiringRowProps) {
  const convId = firing.conversation_id ?? conversationId ?? null

  const { data: allMessages } = useQuery({
    queryKey: ['chat-messages', convId],
    queryFn: () => (convId != null ? getMessages(convId) : Promise.resolve([])),
    enabled: convId != null,
  })

  const relatedMessages: Message[] = useMemo(() => {
    if (!allMessages || !firing.fired_at) return []
    const firedAt = new Date(firing.fired_at).getTime()
    if (Number.isNaN(firedAt)) return []
    const WINDOW_MS = 30_000
    return allMessages.filter((m) => {
      if (!m.createdAt) return false
      const t = new Date(m.createdAt).getTime()
      if (Number.isNaN(t)) return false
      return Math.abs(t - firedAt) <= WINDOW_MS
    })
  }, [allMessages, firing.fired_at])

  const resultsByCallId: Record<string, Message> = useMemo(() => {
    const map: Record<string, Message> = {}
    for (const m of relatedMessages) {
      if (m.role === 'tool' && m.toolCallId) map[m.toolCallId] = m
    }
    return map
  }, [relatedMessages])

  return (
    <div className="space-y-2 pt-2">
      {firing.match_summary && (
        <div className="text-xs text-neutral-300 whitespace-pre-wrap">
          {firing.match_summary}
        </div>
      )}
      {firing.error && (
        <pre
          data-testid="firing-error-pre"
          className="glass-pre rounded-md p-2 text-xs text-rose-200 whitespace-pre-wrap break-all"
        >
          {firing.error}
        </pre>
      )}
      {relatedMessages.map((m) => {
        const toolCalls = m.toolCalls ?? []
        if (m.role !== 'assistant') return null
        if (toolCalls.length === 0) return null
        if (toolCalls.length === 1) {
          const tc = toolCalls[0]
          return (
            <ToolCallCard
              key={`${m.id}-${tc.id}`}
              toolName={tc.function.name}
              arguments={parseToolArgs(tc.function.arguments)}
              result={resultsByCallId[tc.id]?.content}
              isError={false}
            />
          )
        }
        return (
          <ToolGroupCard
            key={`group-${m.id}`}
            calls={toolCalls}
            resultsByCallId={resultsByCallId}
            parseArgs={parseToolArgs}
          />
        )
      })}
    </div>
  )
}

function FiringRow({
  firing,
  conversationId,
}: {
  firing: FiringTimelineRow
  conversationId?: number | null
}) {
  const [open, setOpen] = useState(false)
  return (
    <div
      id={`firing-${firing.id}`}
      data-testid={`firing-row-${firing.id}`}
      data-expanded={open ? 'true' : 'false'}
      className={clsx(
        'glass-row rounded-xl px-3 py-2',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 text-left"
      >
        <RuleStatusChip status={firing.status} />
        <span className="text-xs text-neutral-500" title={firing.fired_at}>
          {formatRelative(firing.fired_at)}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs text-neutral-200">
          {firing.trigger_summary ?? firing.match_summary ?? ''}
        </span>
        {firing.duration_ms != null && (
          <span className="shrink-0 text-[10px] text-neutral-500">
            {firing.duration_ms}ms
          </span>
        )}
        <span className="shrink-0 text-neutral-500">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <ExpandedFiringRow firing={firing} conversationId={conversationId} />
      )}
    </div>
  )
}

function NoMatchGroupRow({ firings }: { firings: FiringTimelineRow[] }) {
  const [open, setOpen] = useState(false)
  const first = firings[firings.length - 1]
  const last = firings[0]
  return (
    <div
      data-testid="firing-no-match-group"
      className="glass-row rounded-xl px-3 py-2"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 text-left"
      >
        <RuleStatusChip status="no_match" />
        <span className="flex-1 text-xs text-neutral-300">
          {firings.length} checks, 0 matches between {formatClock(first.fired_at)} and{' '}
          {formatClock(last.fired_at)}
        </span>
        <span className="shrink-0 text-neutral-500">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <ul
          data-testid="firing-no-match-group-list"
          className="mt-2 space-y-1 pl-6 text-[11px] text-neutral-500"
        >
          {firings.map((f) => (
            <li key={f.id} className="flex items-center gap-2">
              <span>{formatClock(f.fired_at)}</span>
              <span className="truncate">{f.trigger_summary ?? ''}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function RuleTimeline({
  firings,
  filter,
  conversationId,
}: RuleTimelineProps) {
  const items = useMemo(
    () => buildGroupedItems(firings, filter),
    [firings, filter],
  )

  if (items.length === 0) {
    return (
      <div
        className="glass-panel rounded-2xl p-6 text-center text-sm text-neutral-400"
        data-testid="timeline-empty"
      >
        No firings in this view yet.
      </div>
    )
  }

  return (
    <ul data-testid="rule-timeline" className="space-y-1.5">
      {items.map((item, idx) => {
        if (item.kind === 'firing') {
          return (
            <li key={`firing-${item.firing.id}`}>
              <FiringRow
                firing={item.firing}
                conversationId={conversationId}
              />
            </li>
          )
        }
        return (
          <li key={`group-${idx}-${item.firings[0]?.id ?? 0}`}>
            <NoMatchGroupRow firings={item.firings} />
          </li>
        )
      })}
    </ul>
  )
}

export default RuleTimeline
