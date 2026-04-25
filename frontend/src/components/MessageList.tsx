import { memo, useEffect, useMemo, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import clsx from 'clsx'
import type { InflightTool, Message, ToolCall } from '../types'
import { ToolCallCard } from './ToolCallCard'
import { ToolGroupCard } from './ToolGroupCard'
import { useAppStore } from '../store'

// Stable component for the markdown link override; declared at module
// scope so it doesn't change identity per render and keeps ReactMarkdown
// from re-creating its plugin pipeline.
const _MdLink = (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
  <a {...props} target="_blank" rel="noopener noreferrer" />
)
const MARKDOWN_COMPONENTS = { a: _MdLink } as const
const REMARK_PLUGINS = [remarkGfm]

export interface MessageListProps {
  messages: Message[]
  inflightTools?: InflightTool[]
}

interface ToolResultLookup {
  [toolCallId: string]: Message
}

function parseToolArgs(raw: string | undefined): Record<string, unknown> | string {
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

/**
 * Detect a synthetic user turn seeded by the rule engine. The engine formats
 * messages as `[rule trigger] <action_prompt>. Context: <summary>` or the
 * more compact `[rule:<id>] ...` variant. Returns the description portion
 * suitable for a small badge above the bubble, or `null` when not a rule
 * trigger.
 */
function extractRuleTrigger(content: string): string | null {
  if (!content) return null
  const trimmed = content.trimStart()
  if (trimmed.startsWith('[rule trigger]')) {
    const rest = trimmed.slice('[rule trigger]'.length).trim()
    if (!rest) return null
    // Prefer the action prompt up to "Context:" if present.
    const cutoff = rest.indexOf('Context:')
    const head = (cutoff >= 0 ? rest.slice(0, cutoff) : rest).trim()
    return head.replace(/\.$/, '') || rest
  }
  const rulePrefix = trimmed.match(/^\[rule(?::[^\]]+)?\]/)
  if (rulePrefix) {
    const rest = trimmed.slice(rulePrefix[0].length).trim()
    if (!rest) return null
    const cutoff = rest.indexOf('Context:')
    const head = (cutoff >= 0 ? rest.slice(0, cutoff) : rest).trim()
    return head.replace(/\.$/, '') || rest
  }
  return null
}

// ---------------------------------------------------------------------------
// Per-row components — extracted + memoized so streaming-token updates only
// re-render the last assistant row, not every prior tool-call card.
// ---------------------------------------------------------------------------

interface UserRowProps {
  content: string
}

const UserRow = memo(function UserRow({ content }: UserRowProps) {
  const ruleTrigger = extractRuleTrigger(content)
  return (
    <div data-testid="message-user" className="flex flex-col items-end gap-1">
      {ruleTrigger && (
        <div
          data-testid="message-rule-trigger-badge"
          className="glass-row flex max-w-[80%] items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10px] uppercase tracking-wider text-amber-200"
        >
          <svg
            viewBox="0 0 16 16"
            fill="currentColor"
            className="h-3 w-3 text-amber-300"
            aria-hidden
          >
            <path d="M9.5 1.5a.5.5 0 0 1 .5.4L9 7h4a.5.5 0 0 1 .4.8l-7 8a.5.5 0 0 1-.87-.4L7 9H3a.5.5 0 0 1-.4-.8l6.5-6.5a.5.5 0 0 1 .4-.2Z" />
          </svg>
          <span className="truncate normal-case tracking-normal text-[11px] text-amber-100">
            Rule fired · {ruleTrigger}
          </span>
        </div>
      )}
      <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl bg-gradient-to-br from-blue-500/85 to-blue-600/75 backdrop-blur-md ring-1 ring-blue-300/25 px-4 py-2 text-white shadow-lg shadow-blue-500/20">
        {content}
      </div>
    </div>
  )
})

interface SystemRowProps {
  content: string
}

const SystemRow = memo(function SystemRow({ content }: SystemRowProps) {
  return <div className="self-center text-xs text-neutral-500">{content}</div>
})

interface AssistantRowProps {
  message: Message
  inflightTools: InflightTool[]
  resultsByCallId: ToolResultLookup
  showToolDetails: boolean
}

interface UnifiedToolCall {
  id: string
  name: string
  args: Record<string, unknown> | string
  result?: string
  isError: boolean
  status: 'pending' | 'success' | 'error'
}

function buildUnifiedToolCalls(
  toolCalls: ToolCall[],
  inflightTools: InflightTool[],
  resultsByCallId: ToolResultLookup,
): UnifiedToolCall[] {
  // Index already-known tool calls by id so the merge is stable.
  const seen = new Set<string>()
  const merged: UnifiedToolCall[] = []
  for (const tc of toolCalls) {
    seen.add(tc.id)
    const resultMsg = resultsByCallId[tc.id]
    const hasResult = resultMsg !== undefined
    merged.push({
      id: tc.id,
      name: tc.function.name,
      args: parseToolArgs(tc.function.arguments),
      result: hasResult ? resultMsg.content : undefined,
      isError: false,
      status: hasResult ? 'success' : 'pending',
    })
  }
  // Inflight tools whose tool_call_id isn't yet in the assistant message
  // get rendered in the same slot as a pending card. When the result lands
  // and the parent message is updated, the toolCalls branch above takes
  // over with the SAME id — React keeps the DOM mounted and the card
  // smoothly transitions from amber (pending) to emerald (success).
  for (const inflight of inflightTools) {
    if (seen.has(inflight.tool_call_id)) continue
    merged.push({
      id: inflight.tool_call_id,
      name: inflight.tool_name,
      args: inflight.arguments,
      result: undefined,
      isError: false,
      status: 'pending',
    })
  }
  return merged
}

const AssistantRow = memo(function AssistantRow({
  message,
  inflightTools,
  resultsByCallId,
  showToolDetails,
}: AssistantRowProps) {
  const toolCalls = message.toolCalls ?? []
  const unified = buildUnifiedToolCalls(toolCalls, inflightTools, resultsByCallId)
  const pendingCount = unified.filter((u) => u.status === 'pending').length
  return (
    <div
      data-testid="message-assistant"
      className="flex flex-col items-start gap-2"
    >
      {message.content && (
        <div
          className={clsx(
            'glass-panel max-w-[85%] rounded-2xl px-4 py-2 text-neutral-100',
            'prose prose-invert prose-sm max-w-none prose-p:my-1 prose-pre:my-2',
          )}
        >
          <ReactMarkdown
            remarkPlugins={REMARK_PLUGINS}
            components={MARKDOWN_COMPONENTS}
          >
            {message.content}
          </ReactMarkdown>
          {message.isStreaming && (
            <span
              data-testid="streaming-cursor"
              className="ml-0.5 inline-block h-4 w-2 translate-y-0.5 animate-pulse bg-blue-200/80"
            />
          )}
        </div>
      )}
      {unified.length === 1 && (
        <div className="w-full max-w-[85%]">
          {(() => {
            const u = unified[0]
            return (
              <ToolCallCard
                key={u.id}
                toolName={u.name}
                arguments={u.args}
                result={u.result}
                isError={u.isError}
                status={u.status}
                compact={!showToolDetails}
              />
            )
          })()}
        </div>
      )}
      {unified.length > 1 && (
        <div className="w-full max-w-[85%]">
          <ToolGroupCard
            calls={unified.map((u) => ({
              id: u.id,
              type: 'function' as const,
              function: {
                name: u.name,
                arguments:
                  typeof u.args === 'string'
                    ? u.args
                    : JSON.stringify(u.args ?? {}),
              },
            }))}
            resultsByCallId={resultsByCallId}
            parseArgs={parseToolArgs}
            compact={!showToolDetails}
            inflightStatuses={Object.fromEntries(
              unified.map((u) => [u.id, u.status] as const),
            )}
          />
        </div>
      )}
      {pendingCount > 0 && (
        <div
          className="flex items-center gap-2 px-1 text-xs text-neutral-400"
          role="status"
          aria-live="polite"
          data-testid="inflight-tools"
        >
          <span className="inline-flex gap-1">
            <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-amber-400 [animation-delay:-0.3s]" />
            <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-amber-400 [animation-delay:-0.15s]" />
            <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-amber-400" />
          </span>
          Running {pendingCount} tool{pendingCount > 1 ? 's' : ''}…
        </div>
      )}
    </div>
  )
})

const EMPTY_INFLIGHT: InflightTool[] = []

export function MessageList({ messages, inflightTools = EMPTY_INFLIGHT }: MessageListProps) {
  const showToolDetails = useAppStore(
    (s) => s.settings?.show_tool_details ?? false,
  )
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, inflightTools])

  const resultsByCallId: ToolResultLookup = useMemo(() => {
    const map: ToolResultLookup = {}
    for (const m of messages) {
      if (m.role === 'tool' && m.toolCallId) {
        map[m.toolCallId] = m
      }
    }
    return map
  }, [messages])

  return (
    <div
      ref={scrollRef}
      data-testid="message-list"
      className="flex h-full flex-col gap-3 overflow-y-auto px-4 py-4"
    >
      {messages.map((m, idx) => {
        if (m.role === 'tool') return null
        if (m.role === 'system') {
          return <SystemRow key={m.id} content={m.content} />
        }
        if (m.role === 'user') {
          return <UserRow key={m.id} content={m.content} />
        }
        const isLastAssistant = idx === messages.length - 1
        const inflightHere = isLastAssistant ? inflightTools : EMPTY_INFLIGHT
        return (
          <AssistantRow
            key={m.id}
            message={m}
            inflightTools={inflightHere}
            resultsByCallId={resultsByCallId}
            showToolDetails={showToolDetails}
          />
        )
      })}
    </div>
  )
}

export default MessageList
