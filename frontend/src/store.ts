import { create } from 'zustand'
import type {
  Chat,
  InflightTool,
  Message,
  PendingApproval,
  Settings,
  ToolCall,
} from './types'

export type { InflightTool, Message, PendingApproval } from './types'

interface AppState {
  activeChatId: number | null
  chats: Chat[]
  messagesByChat: Record<number, Message[]>
  pendingApprovals: Record<number, PendingApproval[]>
  inflightToolsByChat: Record<number, InflightTool[]>
  settings: Settings | null
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
   * Attach a tool_call to the currently-streaming assistant message so the
   * unified merge in MessageList has a stable parent for the card from the
   * moment `tool_call_start` fires. Without this, the card would unmount
   * the instant `tool_call_result` removes it from `inflightToolsByChat`,
   * because the assistant Message row carrying `toolCalls` only lands
   * after `done` triggers a full history refetch.
   */
  addAssistantToolCall: (chatId: number, toolCall: ToolCall) => void
  addInflightTool: (chatId: number, tool: InflightTool) => void
  removeInflightTool: (chatId: number, tool_call_id: string) => void
  clearInflightTools: (chatId: number) => void
  addPendingApproval: (chatId: number, pa: PendingApproval) => void
  removePendingApproval: (chatId: number, tool_call_id: string) => void
  setSettings: (s: Settings) => void
  markRuleActivitySeen: (ruleId: number) => void
}

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

export const useAppStore = create<AppState>((set) => ({
  activeChatId: null,
  chats: [],
  messagesByChat: {},
  pendingApprovals: {},
  inflightToolsByChat: {},
  settings: null,
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
      if (prev.length === 0) return state
      const lastIdx = prev.length - 1
      const last = prev[lastIdx]
      if (last.role !== 'assistant' || !last.isStreaming) return state
      const next = prev.slice()
      next[lastIdx] = { ...last, content: last.content + delta }
      return { messagesByChat: { ...state.messagesByChat, [chatId]: next } }
    }),

  finalizeStreamingMessage: (chatId) =>
    set((state) => {
      const prev = state.messagesByChat[chatId] ?? []
      if (prev.length === 0) return state
      const lastIdx = prev.length - 1
      const last = prev[lastIdx]
      if (last.role !== 'assistant' || !last.isStreaming) return state
      const next = prev.slice()
      next[lastIdx] = { ...last, isStreaming: false }
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
}))
