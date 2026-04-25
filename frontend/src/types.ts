export type MessageRole = 'user' | 'assistant' | 'tool' | 'system'

export interface ToolCall {
  id: string
  type: 'function'
  function: { name: string; arguments: string }
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

export interface Settings {
  default_model: string
  system_prompt: string
  auto_approve_tools: string[]
  yolo_mode: boolean
  show_tool_details: boolean
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
