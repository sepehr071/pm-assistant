import type {
  Chat,
  Integration,
  Message,
  MessageRole,
  Settings,
  ServerStatus,
  ToolCall,
} from '../types'
import type {
  CreateRuleBody,
  FiringTimelineRow,
  Rule,
  RuleDigest,
  RuleFiringStatus,
  RuleHealth,
  UpdateRuleBody,
} from '../types/rules'

interface RawChat {
  id: number
  title: string
  created_at: string
  updated_at: string
  kind?: string | null
}

interface RawMessage {
  id: number | string
  chat_id?: number
  role: MessageRole
  content: string
  tool_calls?: ToolCall[] | string | null
  tool_call_id?: string | null
  name?: string | null
  created_at?: string
}

function parseToolCalls(raw: ToolCall[] | string | null | undefined): ToolCall[] | undefined {
  if (raw == null) return undefined
  if (Array.isArray(raw)) return raw
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as ToolCall[]) : undefined
  } catch {
    return undefined
  }
}

async function request<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    let message = `Request failed: ${res.status}`
    try {
      const body = await res.json()
      if (body && typeof body === 'object') {
        const detail =
          (body as { detail?: unknown; message?: unknown; error?: unknown })
            .detail ??
          (body as { message?: unknown }).message ??
          (body as { error?: unknown }).error
        if (typeof detail === 'string') message = detail
        else if (detail) message = JSON.stringify(detail)
      }
    } catch {
      // body wasn't JSON, keep default message
    }
    throw new Error(message)
  }
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return text ? (JSON.parse(text) as T) : (undefined as T)
}

function mapChat(raw: RawChat): Chat {
  return {
    id: raw.id,
    title: raw.title,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    kind: (raw.kind ?? 'user') as Chat['kind'],
  }
}

function mapMessage(raw: RawMessage): Message {
  return {
    id: raw.id,
    chatId: raw.chat_id,
    role: raw.role,
    content: raw.content ?? '',
    toolCalls: parseToolCalls(raw.tool_calls),
    toolCallId: raw.tool_call_id ?? undefined,
    name: raw.name ?? undefined,
    createdAt: raw.created_at,
  }
}

export async function listChats(): Promise<Chat[]> {
  const raw = await request<RawChat[]>('/api/chats')
  return raw.map(mapChat)
}

export async function createChat(title?: string): Promise<Chat> {
  const raw = await request<RawChat>('/api/chats', {
    method: 'POST',
    body: JSON.stringify({ title: title ?? null }),
  })
  return mapChat(raw)
}

export async function getMessages(chatId: number): Promise<Message[]> {
  const raw = await request<RawMessage[]>(`/api/chats/${chatId}/messages`)
  return raw.map(mapMessage)
}

export async function deleteChat(chatId: number): Promise<void> {
  await request<void>(`/api/chats/${chatId}`, { method: 'DELETE' })
}

export async function renameChat(
  chatId: number,
  title: string,
): Promise<Chat> {
  const raw = await request<RawChat>(`/api/chats/${chatId}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
  return mapChat(raw)
}

export async function getSettings(): Promise<Settings> {
  return request<Settings>('/api/settings')
}

export async function patchSettings(
  patch: Partial<Settings>,
): Promise<Settings> {
  return request<Settings>('/api/settings', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export async function getMcpStatus(): Promise<ServerStatus[]> {
  return request<ServerStatus[]>('/api/mcp/status')
}

export async function approveTool(
  chatId: number,
  tool_call_id: string,
  approved: boolean,
): Promise<void> {
  await request<void>(`/api/chats/${chatId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ tool_call_id, approved }),
  })
}

export async function listIntegrations(): Promise<Integration[]> {
  const res = await request<{ integrations: Integration[] }>(
    '/api/integrations',
  )
  return res.integrations ?? []
}

export async function connectIntegration(name: string): Promise<Integration> {
  return request<Integration>(
    `/api/integrations/${encodeURIComponent(name)}/connect`,
    { method: 'POST' },
  )
}

export async function refreshIntegration(name: string): Promise<Integration> {
  return request<Integration>(
    `/api/integrations/${encodeURIComponent(name)}/refresh`,
    { method: 'POST' },
  )
}

export async function disconnectIntegration(
  name: string,
): Promise<Integration> {
  return request<Integration>(
    `/api/integrations/${encodeURIComponent(name)}/disconnect`,
    { method: 'POST' },
  )
}

// ---------------------------------------------------------------------------
// Proactive Rules — backend `backend/api/rules.py`
// ---------------------------------------------------------------------------

export async function listRules(): Promise<Rule[]> {
  const raw = await request<Rule[] | { rules: Rule[] }>('/api/rules')
  if (Array.isArray(raw)) return raw
  return raw?.rules ?? []
}

export async function createRule(body: CreateRuleBody): Promise<Rule> {
  return request<Rule>('/api/rules', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function updateRule(
  id: number,
  patch: UpdateRuleBody,
): Promise<Rule> {
  return request<Rule>(`/api/rules/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export async function deleteRule(id: number): Promise<void> {
  await request<void>(`/api/rules/${id}`, { method: 'DELETE' })
}

export async function runRuleNow(id: number): Promise<void> {
  await request<void>(`/api/rules/${id}/run-now`, { method: 'POST' })
}

// ---------------------------------------------------------------------------
// Rule health / firings / digest — backend endpoints added in Track B.
// ---------------------------------------------------------------------------

export interface GetRuleFiringsParams {
  limit?: number
  /** Single status or comma-separated list of statuses. */
  status?: RuleFiringStatus | RuleFiringStatus[] | string
  since?: string
}

export async function getRuleFirings(
  ruleId: number,
  params: GetRuleFiringsParams = {},
): Promise<FiringTimelineRow[]> {
  const qs = new URLSearchParams()
  if (params.limit != null) qs.set('limit', String(params.limit))
  if (params.status != null) {
    const s = Array.isArray(params.status) ? params.status.join(',') : params.status
    if (s) qs.set('status', s)
  }
  if (params.since) qs.set('since', params.since)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  const raw = await request<FiringTimelineRow[] | { firings: FiringTimelineRow[] }>(
    `/api/rules/${ruleId}/firings${suffix}`,
  )
  if (Array.isArray(raw)) return raw
  return raw?.firings ?? []
}

export async function getRuleDigest(
  ruleId: number,
  window: string = '24h',
): Promise<RuleDigest> {
  const qs = new URLSearchParams({ window })
  return request<RuleDigest>(`/api/rules/${ruleId}/digest?${qs.toString()}`)
}

export async function listRuleHealth(): Promise<RuleHealth[]> {
  const raw = await request<RuleHealth[] | { rules: RuleHealth[] }>(
    '/api/rules/health',
  )
  if (Array.isArray(raw)) return raw
  return raw?.rules ?? []
}
