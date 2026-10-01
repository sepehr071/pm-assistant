import { getMessages } from './api'
import type { Chat, Message, ToolCall } from '../types'

// Single tool-result body longer than this gets clipped with a marker so the
// export stays readable and doesn't balloon on multi-megabyte API payloads.
const RESULT_TRUNCATE_LIMIT = 4000

const GENERIC_TITLE = 'Conversation'

function chatTitle(chat: Chat): string {
  const t = (chat.title ?? '').trim()
  return t || GENERIC_TITLE
}

/**
 * Pretty-print a JSON string for embedding in a fenced ```json block. Tool-call
 * arguments arrive as a JSON string from the API; if it does not parse (some
 * providers emit partial / non-JSON), fall back to the raw text verbatim.
 */
function prettyJson(raw: string): string {
  const trimmed = (raw ?? '').trim()
  if (!trimmed) return '{}'
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2)
  } catch {
    return trimmed
  }
}

function clip(text: string): string {
  if (text.length <= RESULT_TRUNCATE_LIMIT) return text
  return `${text.slice(0, RESULT_TRUNCATE_LIMIT)}\n… [truncated]`
}

function roleHeading(role: Message['role']): string {
  switch (role) {
    case 'user':
      return '## User'
    case 'assistant':
      return '## Assistant'
    case 'system':
      return '## System'
    case 'tool':
      return '## Tool'
    default:
      return `## ${role}`
  }
}

/**
 * Render one tool call (and its matching result, if present) as a small,
 * collapsible-friendly block: a heading with the qualified tool name, fenced
 * json of the arguments, then the (possibly truncated) result.
 */
function renderToolCall(call: ToolCall, result: Message | undefined): string {
  const lines: string[] = []
  lines.push(`### 🔧 ${call.function.name}`)
  lines.push('')
  lines.push('Arguments:')
  lines.push('```json')
  lines.push(prettyJson(call.function.arguments))
  lines.push('```')
  if (result) {
    lines.push('')
    lines.push('Result:')
    lines.push('```json')
    lines.push(clip(result.content ?? ''))
    lines.push('```')
  }
  return lines.join('\n')
}

/**
 * Build a Markdown document for a chat. Pure function over the chat metadata and
 * its messages so it is unit-testable without DOM or network.
 *
 * Layout: `# {title}` + ISO date header, then one `## User` / `## Assistant`
 * section per turn in chronological order with raw markdown preserved. Tool
 * calls on an assistant turn are appended as `### 🔧 {tool}` blocks pairing the
 * fenced-json arguments with the fenced-json result (matched by toolCallId).
 */
export function buildMarkdown(chat: Chat, messages: Message[]): string {
  // Index tool-result rows by the call id they answer so each assistant tool
  // call can be paired with its output.
  const resultsByCallId = new Map<string, Message>()
  for (const m of messages) {
    if (m.role === 'tool' && m.toolCallId) {
      resultsByCallId.set(m.toolCallId, m)
    }
  }

  const out: string[] = []
  out.push(`# ${chatTitle(chat)}`)
  const iso = chat.createdAt || chat.updatedAt || ''
  if (iso) out.push(`_${iso}_`)
  out.push('')

  for (const m of messages) {
    // Tool-result rows are folded into their assistant turn, not emitted as
    // standalone sections.
    if (m.role === 'tool') continue

    out.push(roleHeading(m.role))
    out.push('')
    const content = (m.content ?? '').trim()
    if (content) {
      out.push(content)
      out.push('')
    }

    if (m.role === 'assistant' && m.toolCalls && m.toolCalls.length > 0) {
      for (const call of m.toolCalls) {
        out.push(renderToolCall(call, resultsByCallId.get(call.id)))
        out.push('')
      }
    }
  }

  // Trim trailing blank lines down to a single terminating newline.
  return `${out.join('\n').replace(/\n+$/, '')}\n`
}

/**
 * Pretty-printed JSON array of the message objects, matching the shape the
 * frontend received from the API. We export the camelCased `Message[]` the app
 * already holds rather than re-fetching the raw snake_case payload.
 */
export function buildJson(messages: Message[]): string {
  return JSON.stringify(messages, null, 2)
}

/**
 * Turn a chat title into a filesystem-safe filename stem. Strips path
 * separators and characters illegal on Windows, collapses whitespace, and
 * falls back to "chat" when nothing usable remains. Unicode (e.g. Persian) is
 * preserved.
 */
export function sanitizeFilename(title: string | null | undefined): string {
  const cleaned = (title ?? '')
    // Path separators / characters illegal on Windows filenames.
    .replace(/[/\\:*?"<>|]/g, ' ')
    // ASCII control characters (U+0000–U+001F).
    // eslint-disable-next-line no-control-regex -- explicit control-char range
    .replace(/[\x00-\x1f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    // Trailing dots/spaces are invalid on Windows.
    .replace(/[. ]+$/, '')
  return cleaned || 'chat'
}

function triggerDownload(filename: string, mime: string, content: string): void {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke on the next tick so the click has a chance to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

export type ExportFormat = 'markdown' | 'json'

/**
 * Fetch a chat's messages and download it client-side in the requested format.
 * Works for any chat kind (including rule-activity audit timelines), which
 * export with the same structure as user chats.
 */
export async function exportChat(chat: Chat, format: ExportFormat): Promise<void> {
  const messages = await getMessages(chat.id)
  const stem = sanitizeFilename(chat.title)
  if (format === 'json') {
    triggerDownload(`${stem}.json`, 'application/json', buildJson(messages))
  } else {
    triggerDownload(`${stem}.md`, 'text/markdown', buildMarkdown(chat, messages))
  }
}
