import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import MessageList from '../components/MessageList'
import Composer from '../components/Composer'
import ToolApproval from '../components/ToolApproval'
import { approveTool, getMessages, getSettings, patchSettings } from '../lib/api'
import { streamMessage, STREAM_ENDED_UNEXPECTEDLY } from '../lib/stream'
import { useAppStore } from '../store'
import { useT } from '../i18n/useT'
import {
  formatTokenCount,
  formatCost,
  type InflightTool,
  type Message,
  type PendingApproval,
} from '../types'

const EMPTY_MESSAGES: Message[] = []
const EMPTY_PENDING: PendingApproval[] = []
const EMPTY_INFLIGHT: InflightTool[] = []

export default function Chat() {
  const { t } = useT()
  const params = useParams<{ id: string }>()
  const chatId = params.id ? Number(params.id) : null
  const qc = useQueryClient()

  const [sending, setSending] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)
  // Last user text of the current chat, kept so the error strip's Retry
  // button can re-send without the user retyping.
  const [lastUserText, setLastUserText] = useState<string | null>(null)

  const setActiveChat = useAppStore((s) => s.setActiveChat)
  const stampAssistantMessageId = useAppStore((s) => s.stampAssistantMessageId)
  const pushToast = useAppStore((s) => s.pushToast)
  const setMessages = useAppStore((s) => s.setMessages)
  const appendMessage = useAppStore((s) => s.appendMessage)
  const updateLastAssistantDelta = useAppStore((s) => s.updateLastAssistantDelta)
  const finalizeStreamingMessage = useAppStore((s) => s.finalizeStreamingMessage)
  const addToolCallMessage = useAppStore((s) => s.addToolCallMessage)
  const setMessageUsage = useAppStore((s) => s.setMessageUsage)
  const markMessageTruncated = useAppStore((s) => s.markMessageTruncated)
  const addPendingApproval = useAppStore((s) => s.addPendingApproval)
  const removePendingApproval = useAppStore((s) => s.removePendingApproval)
  const addInflightTool = useAppStore((s) => s.addInflightTool)
  const clearInflightTools = useAppStore((s) => s.clearInflightTools)
  const addAssistantToolCall = useAppStore((s) => s.addAssistantToolCall)

  const chatKind = useAppStore((s) =>
    chatId != null ? s.chats.find((c) => c.id === chatId)?.kind ?? null : null
  )
  const isReadOnly =
    chatKind === 'rule_activity' || chatKind === 'system_rules_activity'

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
  const usageTotal = useAppStore((s) =>
    chatId != null ? s.usageTotalsByChat[chatId] ?? null : null
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
      setLastUserText(text)
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

      const outcome = await streamMessage(chatId, text, {
        onToken: (delta) => updateLastAssistantDelta(chatId, delta),
        onUsage: (usage) => setMessageUsage(chatId, usage),
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
        onDone: (messageId, opts) => {
          // Flag truncation before finalizing so the inline notice renders
          // under the now-settled assistant bubble.
          if (opts?.truncated) markMessageTruncated(chatId)
          finalizeStreamingMessage(chatId)
          clearInflightTools(chatId)
          setSending(false)
          // Reconcile the optimistic assistant bubble with its persisted id
          // instead of refetching + remapping the whole thread (which caused
          // a scroll-jump every turn). Switching chats still fetches fresh.
          if (messageId) stampAssistantMessageId(chatId, messageId)
          // Mirror the just-completed turn into the messages cache. Without
          // this the ['messages', chatId] cache keeps its pre-turn snapshot;
          // a chat-switch remount would re-seed setMessages() with that stale
          // array and drop the completed turn (and its optimistic tool rows).
          qc.setQueryData(
            ['messages', chatId],
            useAppStore.getState().messagesByChat[chatId] ?? [],
          )
          qc.invalidateQueries({ queryKey: ['chats'] })
        },
        onError: (err) => {
          finalizeStreamingMessage(chatId)
          clearInflightTools(chatId)
          setSending(false)
          // Map the "connection died mid-turn" sentinel to a localized line;
          // any other error string is shown as-is.
          const shown =
            err === STREAM_ENDED_UNEXPECTEDLY
              ? t('chat.streamEndedUnexpectedly')
              : err
          setStreamError(shown)
          // Surface via the global toaster too, so a stream that errors after
          // the user navigated away from this chat is still visible.
          if (useAppStore.getState().activeChatId !== chatId) {
            pushToast({ kind: 'error', text: t('toast.streamError', { error: shown }) })
          }
        },
        onTitleUpdated: () => {
          qc.invalidateQueries({ queryKey: ['chats'] })
        },
      })

      // A 409 means another turn is already streaming for this chat. The
      // optimistic user+assistant bubbles we appended don't belong, and the
      // user's draft must survive. Roll back the bubbles, toast, then rethrow
      // so the Composer restores the text it optimistically cleared.
      if (!outcome.ok && outcome.reason === 'turn_in_progress') {
        clearInflightTools(chatId)
        setSending(false)
        // Drop the optimistic user + assistant bubbles we appended above.
        const current =
          useAppStore.getState().messagesByChat[chatId] ?? []
        setMessages(
          chatId,
          current.filter(
            (m) => m.id !== userMsg.id && m.id !== assistantMsg.id,
          ),
        )
        pushToast({ kind: 'error', text: t('toast.turnInProgress') })
        throw new Error('turn_in_progress')
      }
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
      setMessageUsage,
      markMessageTruncated,
      finalizeStreamingMessage,
      stampAssistantMessageId,
      setMessages,
      pushToast,
      t,
      qc,
    ]
  )

  const handleRetry = useCallback(() => {
    if (lastUserText == null || sending) return
    setStreamError(null)
    // handleSend rejects on a 409 (turn already in progress); swallow it here
    // since the toast already surfaced the reason.
    handleSend(lastUserText).catch(() => {})
  }, [lastUserText, sending, handleSend])

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
        {t('chat.empty')}
      </div>
    )
  }

  const active = pending[0]

  return (
    <div className="flex h-full min-h-0 flex-col p-4 md:p-6">
      <div className="glass-panel mx-auto flex h-full w-full max-w-4xl min-h-0 flex-col rounded-3xl overflow-hidden">
        {usageTotal && usageTotal.total_tokens > 0 && (
          <div
            data-testid="chat-usage-total"
            className="flex items-center justify-end border-b border-white/5 px-4 py-1.5 text-[11px] tabular-nums text-neutral-500"
          >
            <span dir="ltr">
              {(() => {
                const tokens = formatTokenCount(usageTotal.total_tokens)
                const cost = formatCost(usageTotal.cost)
                return cost
                  ? t('chat.usageTotalCost', { tokens, cost })
                  : t('chat.usageTotal', { tokens })
              })()}
            </span>
          </div>
        )}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {isLoading && (
            <div className="p-6 text-sm text-neutral-500">{t('chat.loadingMessages')}</div>
          )}
          {isError && (
            <div className="p-6 text-sm text-red-400">
              {t('chat.failedToLoad', { error: (error as Error).message })}
            </div>
          )}
          {!isLoading && !isError && messages.length === 0 && !isReadOnly && (
            <div className="flex h-full flex-col items-center justify-center gap-5 px-6 text-center">
              <div>
                <h2 className="text-xl font-bold text-neutral-50">{t('chat.emptyTitle')}</h2>
                <p className="mt-1.5 max-w-md text-sm leading-relaxed text-neutral-400">
                  {t('chat.emptyHint')}
                </p>
              </div>
              <ul className="grid w-full max-w-lg gap-2 text-start text-sm text-neutral-300">
                {(['chat.example1', 'chat.example2', 'chat.example3'] as const).map((k) => (
                  <li key={k} className="glass-pre rounded-xl px-4 py-2.5">
                    {t(k)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!isLoading && !isError && (messages.length > 0 || isReadOnly) && (
            <MessageList messages={messages} inflightTools={inflightTools} />
          )}
        </div>

        {streamError && (
          <div className="flex items-center gap-3 border-t border-red-500/30 bg-red-950/30 px-4 py-2 text-xs text-red-300">
            <span dir="auto" className="min-w-0 flex-1 break-words">
              {t('chat.streamError', { error: streamError })}
            </span>
            {lastUserText != null && (
              <button
                type="button"
                data-testid="stream-retry"
                onClick={handleRetry}
                disabled={sending}
                className="glass-input shrink-0 rounded-md px-2.5 py-1 text-xs font-medium text-red-100 hover:bg-white/10 disabled:opacity-50"
              >
                {t('common.retry')}
              </button>
            )}
          </div>
        )}

        {isReadOnly ? (
          <div className="border-t border-white/10 px-4 py-3 text-center text-xs text-neutral-500">
            {t('chat.readOnlyRuleActivity')}
          </div>
        ) : (
          <Composer onSend={handleSend} disabled={sending} />
        )}
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
