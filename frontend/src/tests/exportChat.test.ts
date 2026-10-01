import { describe, it, expect } from 'vitest'
import { buildMarkdown, sanitizeFilename } from '../lib/exportChat'
import type { Chat, Message } from '../types'

// ---------------------------------------------------------------------------
// Fixtures. The markdown builder is a pure function over (chat, messages) so it
// is trivially testable without rendering or network. Messages mirror the shape
// returned by getMessages(): assistant turns carry `toolCalls`, and each tool
// result is a separate `role: 'tool'` row linked back by `toolCallId`.
// ---------------------------------------------------------------------------

const chat: Chat = {
  id: 7,
  title: 'Sprint planning',
  createdAt: '2026-06-01T08:00:00Z',
  updatedAt: '2026-06-01T09:30:00Z',
  kind: 'user',
}

function msg(over: Partial<Message> & Pick<Message, 'id' | 'role'>): Message {
  return { content: '', ...over }
}

describe('buildMarkdown', () => {
  it('renders a title and an ISO date header', () => {
    const md = buildMarkdown(chat, [])
    expect(md).toContain('# Sprint planning')
    // ISO date (date portion) of the chat creation timestamp.
    expect(md).toContain('2026-06-01')
  })

  it('falls back to a generic title when the chat has none', () => {
    const md = buildMarkdown({ ...chat, title: '' }, [])
    expect(md).toMatch(/^# /m)
  })

  it('emits ## User and ## Assistant sections with raw markdown preserved', () => {
    const messages: Message[] = [
      msg({ id: 1, role: 'user', content: 'Please **summarize** the sprint.' }),
      msg({ id: 2, role: 'assistant', content: 'Here is a `list`:\n- one\n- two' }),
    ]
    const md = buildMarkdown(chat, messages)
    expect(md).toContain('## User')
    expect(md).toContain('## Assistant')
    // Raw markdown must survive untouched (not escaped / rendered).
    expect(md).toContain('Please **summarize** the sprint.')
    expect(md).toContain('Here is a `list`:')
    expect(md).toContain('- one')
  })

  it('preserves chronological order of turns', () => {
    const messages: Message[] = [
      msg({ id: 1, role: 'user', content: 'first' }),
      msg({ id: 2, role: 'assistant', content: 'second' }),
      msg({ id: 3, role: 'user', content: 'third' }),
    ]
    const md = buildMarkdown(chat, messages)
    const iFirst = md.indexOf('first')
    const iSecond = md.indexOf('second')
    const iThird = md.indexOf('third')
    expect(iFirst).toBeGreaterThanOrEqual(0)
    expect(iFirst).toBeLessThan(iSecond)
    expect(iSecond).toBeLessThan(iThird)
  })

  it('renders a tool call as a heading with fenced json args and result', () => {
    const messages: Message[] = [
      msg({
        id: 1,
        role: 'assistant',
        content: 'Looking it up.',
        toolCalls: [
          {
            id: 'call_1',
            type: 'function',
            function: {
              name: 'jira__search',
              arguments: '{"jql":"project = PM"}',
            },
          },
        ],
      }),
      msg({
        id: 2,
        role: 'tool',
        content: '{"issues":[{"key":"PM-1"}]}',
        toolCallId: 'call_1',
        name: 'jira__search',
      }),
    ]
    const md = buildMarkdown(chat, messages)
    expect(md).toContain('jira__search')
    // arguments fenced as json
    expect(md).toContain('```json')
    expect(md).toContain('"jql": "project = PM"')
    // result content present
    expect(md).toContain('PM-1')
  })

  it('pretty-prints valid JSON arguments and leaves invalid JSON verbatim', () => {
    const messages: Message[] = [
      msg({
        id: 1,
        role: 'assistant',
        content: '',
        toolCalls: [
          {
            id: 'c1',
            type: 'function',
            function: { name: 'x__y', arguments: 'not json at all' },
          },
        ],
      }),
    ]
    const md = buildMarkdown(chat, messages)
    expect(md).toContain('not json at all')
  })

  it('truncates a single result longer than the cap and marks it', () => {
    const huge = 'x'.repeat(5000)
    const messages: Message[] = [
      msg({
        id: 1,
        role: 'assistant',
        content: '',
        toolCalls: [
          {
            id: 'c1',
            type: 'function',
            function: { name: 'x__y', arguments: '{}' },
          },
        ],
      }),
      msg({ id: 2, role: 'tool', content: huge, toolCallId: 'c1', name: 'x__y' }),
    ]
    const md = buildMarkdown(chat, messages)
    expect(md).toContain('[truncated]')
    // The 5000-char blob must not appear in full.
    expect(md).not.toContain(huge)
  })

  it('handles a tool call with no matching result row', () => {
    const messages: Message[] = [
      msg({
        id: 1,
        role: 'assistant',
        content: '',
        toolCalls: [
          {
            id: 'orphan',
            type: 'function',
            function: { name: 'x__y', arguments: '{}' },
          },
        ],
      }),
    ]
    const md = buildMarkdown(chat, messages)
    expect(md).toContain('x__y')
    // Should not throw and should still produce a document.
    expect(md.length).toBeGreaterThan(0)
  })
})

describe('sanitizeFilename', () => {
  it('strips path separators and illegal characters', () => {
    expect(sanitizeFilename('a/b\\c:d*e?f"g<h>i|j')).not.toMatch(/[/\\:*?"<>|]/)
  })

  it('falls back to "chat" for empty or whitespace-only titles', () => {
    expect(sanitizeFilename('')).toBe('chat')
    expect(sanitizeFilename('   ')).toBe('chat')
  })

  it('keeps unicode (e.g. Persian) characters', () => {
    expect(sanitizeFilename('جلسه برنامه‌ریزی')).toContain('جلسه')
  })
})
