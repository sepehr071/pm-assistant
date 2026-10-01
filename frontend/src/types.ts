export type MessageRole = 'user' | 'assistant' | 'tool' | 'system'

export interface ToolCall {
  id: string
  type: 'function'
  function: { name: string; arguments: string }
}

/**
 * Token-usage accounting for one assistant turn. Accumulated across every
 * agent-loop iteration on the backend and emitted exactly once per turn via
 * the SSE `usage` event, right before `done`. `cost` is null when the model
 * provider doesn't report a price.
 */
export interface Usage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost: number | null
}

/**
 * Compact a token count for display: 1234 -> "1.2k", 980 -> "980".
 * Pure + locale-agnostic so it can be shared by the bubble meta and the
 * per-chat running-total line without dragging i18n state around.
 */
export function formatTokenCount(tokens: number): string {
  if (!Number.isFinite(tokens) || tokens < 0) return '0'
  if (tokens < 1000) return String(Math.round(tokens))
  const k = tokens / 1000
  // One decimal under 100k, whole numbers above to keep it terse.
  return k < 100 ? `${k.toFixed(1)}k` : `${Math.round(k)}k`
}

/**
 * Format a USD cost like "$0.0031". Returns null when cost is null so callers
 * can omit the cost segment entirely rather than print "$0".
 */
export function formatCost(cost: number | null): string | null {
  if (cost == null || !Number.isFinite(cost)) return null
  // Sub-cent costs need 4 dp; larger ones read fine at 4 dp too.
  return `$${cost.toFixed(4)}`
}

export interface Message {
  id: number | string
  chatId?: number
  role: MessageRole
  content: string
  toolCalls?: ToolCall[]
  toolCallId?: string
  name?: string
  isStreaming?: boolean
  createdAt?: string
  /** Per-turn token usage, attached when the `usage` SSE event lands. */
  usage?: Usage
  /**
   * True when the backend hit its tool-step iteration cap and cut the turn
   * short (carried on the `done` payload). Surfaces a subtle inline notice.
   */
  truncated?: boolean
}

export type ChatKind = 'user' | 'system_rules_activity' | 'rule_activity'

export interface Chat {
  id: number
  title: string
  createdAt: string
  updatedAt: string
  kind?: ChatKind
}

export interface PendingApproval {
  tool_call_id: string
  tool_name: string
  arguments: Record<string, unknown>
}

export interface ServerStatus {
  name: string
  healthy: boolean
  error: string | null
  tool_count: number
}

export type Language = 'en' | 'fa'

export interface Settings {
  system_prompt: string
  auto_approve_tools: string[]
  yolo_mode: boolean
  dangerous_always_approve: boolean
  show_tool_details: boolean
  language: Language
}

export interface ToolResult {
  tool_call_id: string
  tool_name: string
  result: string
  is_error: boolean
}

export interface InflightTool {
  tool_call_id: string
  tool_name: string
  arguments: Record<string, unknown>
}

export type IntegrationState =
  | 'connected'
  | 'auth_required'
  | 'input_required'
  | 'error'
  | 'disconnected'
  | 'unconfigured'

export interface Integration {
  name: string
  label: string
  state: IntegrationState
  setup_url?: string | null
  error?: string | null
  tool_count: number
  server_name?: string | null
}
