import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import MessageList from '../components/MessageList'
import Composer from '../components/Composer'
import ToolApproval from '../components/ToolApproval'
import { approveTool, getMessages, getSettings, patchSettings } from '../lib/api'
import { streamMessage } from '../lib/stream'
import { useAppStore } from '../store'
import type { InflightTool, Message, PendingApproval } from '../types'

const EMPTY_MESSAGES: Message[] = []
const EMPTY_PENDING: PendingApproval[] = []
const EMPTY_INFLIGHT: InflightTool[] = []

export default function Chat() {
  const params = useParams<{ id: string }>()
  const chatId = params.id ? Number(params.id) : null
  const qc = useQueryClient()

  const [sending, setSending] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)

  const setActiveChat = useAppStore((s) => s.setActiveChat)
  const setMessages = useAppStore((s) => s.setMessages)
  const appendMessage = useAppStore((s) => s.appendMessage)
  const updateLastAssistantDelta = useAppStore((s) => s.updateLastAssistantDelta)
  const finalizeStreamingMessage = useAppStore((s) => s.finalizeStreamingMessage)
  const addToolCallMessage = useAppStore((s) => s.addToolCallMessage)
  const addPendingApproval = useAppStore((s) => s.addPendingApproval)
  const removePendingApproval = useAppStore((s) => s.removePendingApproval)
  const addInflightTool = useAppStore((s) => s.addInflightTool)
  const clearInflightTools = useAppStore((s) => s.clearInflightTools)
  const addAssistantToolCall = useAppStore((s) => s.addAssistantToolCall)

  const messages = useAppStore((s) =>
    chatId != null ? s.messagesByChat[chatId] ?? EMPTY_MESSAGES : EMPTY_MESSAGES
  )
  const pending = useAppStore((s) =>
    chatId != null ? s.pendingApprovals[chatId] ?? EMPTY_PENDING : EMPTY_PENDING
  )
  const inflightTools = useAppStore((s) =>
    chatId != null
      ? s.inflightToolsByChat[chatId] ?? EMPTY_INFLIGHT
      : EMPTY_INFLIGHT
  )

  useEffect(() => {
    setActiveChat(chatId)
    return () => setActiveChat(null)
  }, [chatId, setActiveChat])

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['messages', chatId],
    queryFn: () => getMessages(chatId as number),
    enabled: chatId != null,
  })

  useEffect(() => {
    if (chatId != null && data) {
      setMessages(chatId, data)
    }
  }, [chatId, data, setMessages])

  const handleSend = useCallback(
    async (text: string) => {
      if (chatId == null || sending) return
      setStreamError(null)
      setSending(true)

      const userMsg: Message = {
        id: `user-${Date.now()}`,
        role: 'user',
        content: text,
      }
      const assistantMsg: Message = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: '',
        isStreaming: true,
      }
      appendMessage(chatId, userMsg)
      appendMessage(chatId, assistantMsg)

      await streamMessage(chatId, text, {
        onToken: (delta) => updateLastAssistantDelta(chatId, delta),
        onToolStart: (tool) => {
          // Anchor the tool_call on the streaming assistant message before
          // it's known on the FE through any other path. Without this the
          // card would unmount the moment `tool_call_result` clears the
          // inflight slot — see plans/do-a-research-and-groovy-puzzle.md.
          addAssistantToolCall(chatId, {
            id: tool.tool_call_id,
            type: 'function',
            function: {
              name: tool.tool_name,
              arguments: JSON.stringify(tool.arguments ?? {}),
            },
          })
          addInflightTool(chatId, tool)
        },
        onToolRequest: (req) => addPendingApproval(chatId, req),
        onToolResult: (res) =>
          addToolCallMessage(chatId, {
            tool_call_id: res.tool_call_id,
            name: res.tool_name,
            result: res.result,
            is_error: res.is_error,
          }),
        onDone: () => {
          finalizeStreamingMessage(chatId)
          clearInflightTools(chatId)
          setSending(false)
          qc.invalidateQueries({ queryKey: ['messages', chatId] })
          qc.invalidateQueries({ queryKey: ['chats'] })
        },
        onError: (err) => {
          finalizeStreamingMessage(chatId)
          clearInflightTools(chatId)
          setSending(false)
          setStreamError(err)
        },
      })
    },
    [
      chatId,
      sending,
      appendMessage,
      updateLastAssistantDelta,
      addInflightTool,
      addAssistantToolCall,
      clearInflightTools,
      addPendingApproval,
      addToolCallMessage,
      finalizeStreamingMessage,
      qc,
    ]
  )

  const handleApprove = useCallback(
    async (toolCallId: string, alwaysApprove: boolean) => {
      if (chatId == null) return
      const pa = pending.find((p) => p.tool_call_id === toolCallId)
      removePendingApproval(chatId, toolCallId)
      try {
        if (alwaysApprove && pa) {
          try {
            const current = await getSettings()
            const list = current.auto_approve_tools ?? []
            if (!list.includes(pa.tool_name)) {
              await patchSettings({
                auto_approve_tools: [...list, pa.tool_name],
              })
            }
          } catch {
            // non-fatal; approval still proceeds
          }
        }
        await approveTool(chatId, toolCallId, true)
      } catch (err) {
        setStreamError((err as Error).message)
      }
    },
    [chatId, pending, removePendingApproval]
  )

  const handleReject = useCallback(
    async (toolCallId: string) => {
      if (chatId == null) return
      removePendingApproval(chatId, toolCallId)
      try {
        await approveTool(chatId, toolCallId, false)
      } catch (err) {
        setStreamError((err as Error).message)
      }
    },
    [chatId, removePendingApproval]
  )

  if (chatId == null) {
    return (
      <div className="flex h-full items-center justify-center text-neutral-500">
        Select or create a chat.
      </div>
    )
  }

  const active = pending[0]

  return (
    <div className="flex h-full min-h-0 flex-col p-4 md:p-6">
      <div className="glass-panel mx-auto flex h-full w-full max-w-4xl min-h-0 flex-col rounded-3xl overflow-hidden">
        <div className="flex-1 min-h-0 overflow-y-auto">
          {isLoading && (
            <div className="p-6 text-sm text-neutral-500">Loading messages…</div>
          )}
          {isError && (
            <div className="p-6 text-sm text-red-400">
              Failed to load: {(error as Error).message}
            </div>
          )}
          {!isLoading && !isError && (
            <MessageList messages={messages} inflightTools={inflightTools} />
          )}
        </div>

        {streamError && (
          <div className="border-t border-red-500/30 bg-red-950/30 px-4 py-2 text-xs text-red-300">
            {streamError}
          </div>
        )}

        <Composer onSend={handleSend} disabled={sending} />
      </div>

      {active && (
        <ToolApproval
          pending={active}
          onApprove={handleApprove}
          onReject={handleReject}
        />
      )}
    </div>
  )
}
