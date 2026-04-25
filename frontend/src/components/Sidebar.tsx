import { useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  createChat,
  deleteChat,
  listChats,
  renameChat,
} from '../lib/api'
import type { Chat } from '../types'

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const diff = Date.now() - then
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}

function isActivityChat(chat: Chat): boolean {
  return chat.kind === 'system_rules_activity' || chat.kind === 'rule_activity'
}

type ChatRowProps = {
  chat: Chat
  active: boolean
  onRename: (chat: Chat) => void
  onDelete: (chat: Chat) => void
}

function ChatRow({ chat, active, onRename, onDelete }: ChatRowProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const ts = chat.updatedAt ?? chat.createdAt
  return (
    <div
      data-active={active}
      className={clsx(
        'glass-row group relative flex items-center justify-between rounded-lg px-2 py-2 text-sm cursor-pointer',
        active ? 'text-neutral-100' : 'text-neutral-300'
      )}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenuOpen((o) => !o)
      }}
    >
      <Link
        to={`/chat/${chat.id}`}
        className="flex-1 min-w-0"
        onClick={() => setMenuOpen(false)}
      >
        <div className="truncate">{chat.title || 'New chat'}</div>
        <div className="text-[11px] text-neutral-500">{formatRelative(ts)}</div>
      </Link>
      <button
        type="button"
        aria-label="Chat actions"
        className={clsx(
          'ml-2 px-1.5 py-0.5 rounded text-neutral-400 hover:text-neutral-100 hover:bg-white/10',
          menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
        )}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setMenuOpen((o) => !o)
        }}
      >
        ⋯
      </button>
      {menuOpen && (
        <div
          className="glass-elevated absolute right-1 top-full z-10 mt-1 w-36 rounded-lg overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="block w-full px-3 py-2 text-left text-sm text-neutral-200 hover:bg-white/10"
            onClick={() => {
              setMenuOpen(false)
              onRename(chat)
            }}
          >
            Rename
          </button>
          <button
            type="button"
            className="block w-full px-3 py-2 text-left text-sm text-red-300 hover:bg-white/10"
            onClick={() => {
              setMenuOpen(false)
              onDelete(chat)
            }}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

export default function Sidebar() {
  const params = useParams<{ id?: string }>()
  const activeId = params.id ? Number(params.id) : null
  const navigate = useNavigate()
  const qc = useQueryClient()
  const location = useLocation()

  const { data: chats } = useQuery({
    queryKey: ['chats'],
    queryFn: listChats,
  })

  const regularChats = (chats ?? []).filter((c) => !isActivityChat(c))

  const createMut = useMutation({
    mutationFn: () => createChat(),
    onSuccess: async (chat) => {
      await qc.invalidateQueries({ queryKey: ['chats'] })
      navigate(`/chat/${chat.id}`)
    },
  })

  const renameMut = useMutation({
    mutationFn: (v: { id: number; title: string }) => renameChat(v.id, v.title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['chats'] }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteChat(id),
    onSuccess: async (_, id) => {
      await qc.invalidateQueries({ queryKey: ['chats'] })
      if (id === activeId) navigate('/', { replace: true })
    },
  })

  const handleRename = (chat: Chat) => {
    const next = window.prompt('Rename chat', chat.title ?? '')
    if (next && next.trim() && next !== chat.title) {
      renameMut.mutate({ id: chat.id, title: next.trim() })
    }
  }

  const handleDelete = (chat: Chat) => {
    if (window.confirm(`Delete "${chat.title || 'New chat'}"?`)) {
      deleteMut.mutate(chat.id)
    }
  }

  const rulesActive = location.pathname.startsWith('/rules')

  return (
    <div className="flex h-full flex-col">
      <div className="p-3 border-b border-white/5">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-sm font-semibold tracking-wide text-neutral-100">
            PM Assistant
          </h1>
        </div>
        <button
          type="button"
          className="glass-input w-full rounded-lg px-3 py-2 text-sm text-neutral-100 hover:bg-white/10 disabled:opacity-50 transition"
          disabled={createMut.isPending}
          onClick={() => createMut.mutate()}
        >
          + New chat
        </button>
      </div>

      <div className="px-2 pt-2">
        <Link
          to="/rules"
          data-testid="nav-rules"
          data-active={rulesActive}
          className={clsx(
            'glass-row flex items-center gap-2 rounded-lg px-2 py-2 text-sm',
            rulesActive ? 'text-neutral-100' : 'text-neutral-300'
          )}
        >
          <span
            aria-hidden
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/5 text-blue-300"
          >
            <svg viewBox="0 0 16 16" fill="currentColor" className="h-4 w-4">
              <path d="M3 2.75A.75.75 0 0 1 3.75 2h8.5A.75.75 0 0 1 13 2.75v10.5a.75.75 0 0 1-1.2.6L8 11.1l-3.8 2.75A.75.75 0 0 1 3 13.25V2.75Z" />
            </svg>
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm">Rules</div>
            <div className="text-[11px] text-neutral-500">
              Proactive triggers
            </div>
          </div>
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
        {regularChats.map((c) => (
          <ChatRow
            key={c.id}
            chat={c}
            active={c.id === activeId}
            onRename={handleRename}
            onDelete={handleDelete}
          />
        ))}
        {(!chats || regularChats.length === 0) && (
          <div className="px-2 py-4 text-xs text-neutral-500">
            No chats yet.
          </div>
        )}
      </div>

      <div className="border-t border-white/5 p-3">
        <Link
          to="/settings"
          className="block rounded-lg px-3 py-2 text-sm text-neutral-300 hover:bg-white/10 transition"
        >
          Settings
        </Link>
      </div>
    </div>
  )
}
