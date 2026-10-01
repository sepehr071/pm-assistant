import { create } from 'zustand'
import type {
  Chat,
  InflightTool,
  Message,
  PendingApproval,
  Settings,
  ToolCall,
  Usage,
} from './types'

export type { InflightTool, Message, PendingApproval } from './types'

export type ToastKind = 'error' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  text: string
}

interface AppState {
  activeChatId: number | null
  chats: Chat[]
  messagesByChat: Record<number, Message[]>
  pendingApprovals: Record<number, PendingApproval[]>
  inflightToolsByChat: Record<number, InflightTool[]>
  /**
   * Per-chat running token total, accumulated across every turn of the
   * session. Display-only, not persisted — resets on reload.
   */
  usageTotalsByChat: Record<number, Usage>
  settings: Settings | null
  /** Transient notification toasts (top-right Toaster). */
  toasts: Toast[]
  /** Per-rule last-seen timestamps (ms) for unread detection on rule activity chats. */
  lastSeenRuleActivityAt: Record<number, number>
  setActiveChat: (id: number | null) => void
  setChats: (chats: Chat[]) => void
  setMessages: (chatId: number, msgs: Message[]) => void
  appendMessage: (chatId: number, msg: Message) => void
  updateLastAssistantDelta: (chatId: number, delta: string) => void
  finalizeStreamingMessage: (chatId: number) => void
  addToolCallMessage: (
    chatId: number,
    tc: { tool_call_id: string; name: string; result: string; is_error: boolean }
  ) => void
  /**
   * Attach per-turn token usage to the last streaming assistant message and
   * fold it into the chat's running total. Fired on the `usage` SSE event.
   */
  setMessageUsage: (chatId: number, usage: Usage) => void
  /**
   * Flag the last streaming assistant message as truncated (the backend hit
   * its tool-step iteration cap). Carried on the `done` payload.
   */
  markMessageTruncated: (chatId: number) => void
  /**
   * Stamp the server-persisted id onto the last streaming assistant message
   * once the `done` event lands. Lets Chat.tsx reconcile optimistic state
   * with the real row id without refetching + remapping the whole thread
   * (which caused scroll flicker every turn).
   */
  stampAssistantMessageId: (chatId: number, messageId: number) => void
  /**
   * Attach a tool_call to the currently-streaming assistant message so the
   * unified merge in MessageList has a stable parent for the card from the
   * moment `tool_call_start` fires. Without this, the card would unmount
   * the instant `tool_call_result` removes it from `inflightToolsByChat`.
   * There is no full history refetch on `done` anymore: the assistant row
   * is stamped in place via `stampAssistantMessageId` and Chat.tsx mirrors
   * the live store thread into the React Query cache; tool-result rows keep
   * their optimistic `tool-${id}` string ids until the messages query is
   * genuinely refetched (e.g. a later chat-switch past staleTime).
   */
  addAssistantToolCall: (chatId: number, toolCall: ToolCall) => void
  addInflightTool: (chatId: number, tool: InflightTool) => void
  removeInflightTool: (chatId: number, tool_call_id: string) => void
  clearInflightTools: (chatId: number) => void
  addPendingApproval: (chatId: number, pa: PendingApproval) => void
  removePendingApproval: (chatId: number, tool_call_id: string) => void
  setSettings: (s: Settings) => void
  markRuleActivitySeen: (ruleId: number) => void
  pushToast: (toast: { kind: ToastKind; text: string }) => number
  dismissToast: (id: number) => void
}

let _toastSeq = 0

const RULE_ACTIVITY_SEEN_PREFIX = 'pm-rule-activity-last-seen:'

function ruleActivityStorageKey(ruleId: number): string {
  return `${RULE_ACTIVITY_SEEN_PREFIX}${ruleId}`
}

function readInitialRuleSeen(): Record<number, number> {
  if (typeof window === 'undefined') return {}
  const out: Record<number, number> = {}
  try {
    for (let i = 0; i < window.localStorage.length; i++) {
      const key = window.localStorage.key(i)
      if (!key || !key.startsWith(RULE_ACTIVITY_SEEN_PREFIX)) continue
      const ruleId = Number(key.slice(RULE_ACTIVITY_SEEN_PREFIX.length))
      if (!Number.isFinite(ruleId)) continue
      const raw = window.localStorage.getItem(key)
      if (!raw) continue
      const n = Number(raw)
      if (Number.isFinite(n)) out[ruleId] = n
    }
  } catch {
    // ignore storage failures
  }
  return out
}

/**
 * Index of the still-streaming assistant bubble, or -1. Tool result rows are
 * appended after it mid-turn, so it is not necessarily the last message.
 */
function lastStreamingAssistantIdx(msgs: Message[]): number {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i]
    if (m.role === 'assistant') return m.isStreaming ? i : -1
  }
  return -1
}

export const useAppStore = create<AppState>((set) => ({
  activeChatId: null,
  chats: [],
  messagesByChat: {},
  pendingApprovals: {},
  inflightToolsByChat: {},
  usageTotalsByChat: {},
  settings: null,
  toasts: [],
  lastSeenRuleActivityAt: readInitialRuleSeen(),

  setActiveChat: (id) => set({ activeChatId: id }),

  setChats: (chats) => set({ chats }),

  setMessages: (chatId, msgs) =>
    set((state) => ({
      messagesByChat: { ...state.messagesByChat, [chatId]: msgs },
    })),

  appendMessage: (chatId, msg) =>
    set((state) => {
      const prev = state.messagesByChat[chatId] ?? []
      return {
        messagesByChat: { ...state.messagesByChat, [chatId]: [...prev, msg] },
      }
    }),

  updateLastAssistantDelta: (chatId, delta) =>
    set((state) => {
      const prev = state.messagesByChat[chatId] ?? []
      const idx = lastStreamingAssistantIdx(prev)
      if (idx === -1) return state
      const next = prev.slice()
      next[idx] = { ...prev[idx], content: prev[idx].content + delta }
      return { messagesByChat: { ...state.messagesByChat, [chatId]: next } }
    }),

  finalizeStreamingMessage: (chatId) =>
    set((state) => {
      const prev = state.messagesByChat[chatId] ?? []
      const idx = lastStreamingAssistantIdx(prev)
      if (idx === -1) return state
      const next = prev.slice()
      next[idx] = { ...prev[idx], isStreaming: false }
      return { messagesByChat: { ...state.messagesByChat, [chatId]: next } }
    }),

  addToolCallMessage: (chatId, tc) =>
    set((state) => {
      const prev = state.messagesByChat[chatId] ?? []
      const msg: Message = {
        id: `tool-${tc.tool_call_id}`,
        role: 'tool',
        content: tc.result,
        toolCallId: tc.tool_call_id,
        name: tc.name,
      }
      const inflight = state.inflightToolsByChat[chatId] ?? []
      const nextInflight = inflight.filter(
        (t) => t.tool_call_id !== tc.tool_call_id,
      )
      return {
        messagesByChat: { ...state.messagesByChat, [chatId]: [...prev, msg] },
        inflightToolsByChat: {
          ...state.inflightToolsByChat,
          [chatId]: nextInflight,
        },
      }
    }),

  setMessageUsage: (chatId, usage) =>
    set((state) => {
      // Fold into the chat running total. `cost` stays null only while every
      // turn so far reported null; once any turn carries a price the total
      // becomes a number.
      const prevTotal = state.usageTotalsByChat[chatId]
      const nextCost =
        usage.cost == null && (prevTotal?.cost ?? null) == null
          ? null
          : (prevTotal?.cost ?? 0) + (usage.cost ?? 0)
      const nextTotal: Usage = {
        prompt_tokens: (prevTotal?.prompt_tokens ?? 0) + usage.prompt_tokens,
        completion_tokens:
          (prevTotal?.completion_tokens ?? 0) + usage.completion_tokens,
        total_tokens: (prevTotal?.total_tokens ?? 0) + usage.total_tokens,
        cost: nextCost,
      }

      // Stamp usage onto the most recent streaming assistant bubble.
      const prev = state.messagesByChat[chatId] ?? []
      let nextMessages = prev
      for (let i = prev.length - 1; i >= 0; i--) {
        const m = prev[i]
        if (m.role !== 'assistant') continue
        if (!m.isStreaming) break
        const copy = prev.slice()
        copy[i] = { ...m, usage }
        nextMessages = copy
        break
      }

      return {
        usageTotalsByChat: {
          ...state.usageTotalsByChat,
          [chatId]: nextTotal,
        },
        messagesByChat:
          nextMessages === prev
            ? state.messagesByChat
            : { ...state.messagesByChat, [chatId]: nextMessages },
      }
    }),

  markMessageTruncated: (chatId) =>
    set((state) => {
      const prev = state.messagesByChat[chatId] ?? []
      for (let i = prev.length - 1; i >= 0; i--) {
        const m = prev[i]
        if (m.role !== 'assistant') continue
        const next = prev.slice()
        next[i] = { ...m, truncated: true }
        return { messagesByChat: { ...state.messagesByChat, [chatId]: next } }
      }
      return state
    }),

  stampAssistantMessageId: (chatId, messageId) =>
    set((state) => {
      const prev = state.messagesByChat[chatId] ?? []
      // Find the latest assistant bubble (the one that just finished
      // streaming) that still carries an optimistic string id.
      for (let i = prev.length - 1; i >= 0; i--) {
        const m = prev[i]
        if (m.role !== 'assistant') continue
        if (typeof m.id === 'number') return state // already persisted
        const next = prev.slice()
        next[i] = { ...m, id: messageId }
        return {
          messagesByChat: { ...state.messagesByChat, [chatId]: next },
        }
      }
      return state
    }),

  addAssistantToolCall: (chatId, toolCall) =>
    set((state) => {
      const prev = state.messagesByChat[chatId] ?? []
      // Walk from the end to find the latest still-streaming assistant
      // bubble. Tool result messages may have been appended in between.
      let targetIdx = -1
      for (let i = prev.length - 1; i >= 0; i--) {
        const m = prev[i]
        if (m.role === 'assistant' && m.isStreaming) {
          targetIdx = i
          break
        }
      }
      if (targetIdx === -1) {
        // Defensive fallback — Chat.tsx normally appends a streaming
        // placeholder before streamMessage runs, so we shouldn't hit this.
        const placeholder: Message = {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: '',
          isStreaming: true,
          toolCalls: [toolCall],
        }
        return {
          messagesByChat: {
            ...state.messagesByChat,
            [chatId]: [...prev, placeholder],
          },
        }
      }
      const target = prev[targetIdx]
      const existing = target.toolCalls ?? []
      if (existing.some((t) => t.id === toolCall.id)) return state
      const next = prev.slice()
      next[targetIdx] = { ...target, toolCalls: [...existing, toolCall] }
      return {
        messagesByChat: { ...state.messagesByChat, [chatId]: next },
      }
    }),

  addInflightTool: (chatId, tool) =>
    set((state) => {
      const prev = state.inflightToolsByChat[chatId] ?? []
      if (prev.some((t) => t.tool_call_id === tool.tool_call_id)) return state
      return {
        inflightToolsByChat: {
          ...state.inflightToolsByChat,
          [chatId]: [...prev, tool],
        },
      }
    }),

  removeInflightTool: (chatId, tool_call_id) =>
    set((state) => {
      const prev = state.inflightToolsByChat[chatId] ?? []
      return {
        inflightToolsByChat: {
          ...state.inflightToolsByChat,
          [chatId]: prev.filter((t) => t.tool_call_id !== tool_call_id),
        },
      }
    }),

  clearInflightTools: (chatId) =>
    set((state) => ({
      inflightToolsByChat: { ...state.inflightToolsByChat, [chatId]: [] },
    })),

  addPendingApproval: (chatId, pa) =>
    set((state) => {
      const prev = state.pendingApprovals[chatId] ?? []
      if (prev.some((p) => p.tool_call_id === pa.tool_call_id)) return state
      return {
        pendingApprovals: {
          ...state.pendingApprovals,
          [chatId]: [...prev, pa],
        },
      }
    }),

  removePendingApproval: (chatId, tool_call_id) =>
    set((state) => {
      const prev = state.pendingApprovals[chatId] ?? []
      const next = prev.filter((p) => p.tool_call_id !== tool_call_id)
      return {
        pendingApprovals: { ...state.pendingApprovals, [chatId]: next },
      }
    }),

  setSettings: (s) => set({ settings: s }),

  markRuleActivitySeen: (ruleId) => {
    const now = Date.now()
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(ruleActivityStorageKey(ruleId), String(now))
      } catch {
        // ignore storage failures (e.g. disabled / private mode)
      }
    }
    set((state) => ({
      lastSeenRuleActivityAt: {
        ...state.lastSeenRuleActivityAt,
        [ruleId]: now,
      },
    }))
  },

  pushToast: ({ kind, text }) => {
    const id = ++_toastSeq
    set((state) => ({ toasts: [...state.toasts, { id, kind, text }] }))
    return id
  },

  dismissToast: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((tst) => tst.id !== id),
    })),
}))
