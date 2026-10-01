import { useState } from 'react'
import { Link, useLocation, useMatch, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  createChat,
  deleteChat,
  listChats,
  renameChat,
} from '../lib/api'
import { exportChat, type ExportFormat } from '../lib/exportChat'
import type { Chat } from '../types'
import { useAppStore } from '../store'
import { useT } from '../i18n/useT'
import type { TKey } from '../i18n'

type T = ReturnType<typeof useT>['t']

function formatRelative(iso: string | null | undefined, t: T, lang: string): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const diff = Date.now() - then
  if (diff < 60_000) return t('sidebar.justNow')
  const m = Math.floor(diff / 60_000)
  if (m < 60) return t('sidebar.minutesShort', { n: m })
  if (m < 60 * 24) return t('sidebar.hoursShort', { n: Math.floor(m / 60) })
  const d = Math.floor(diff / 86_400_000)
  if (d < 7) return t('sidebar.daysShort', { n: d })
  const w = Math.floor(d / 7)
  if (w < 52) return t('sidebar.weeksShort', { n: w })
  return new Date(iso).toLocaleDateString(lang === 'fa' ? 'fa-IR-u-nu-latn' : undefined)
}

function isActivityChat(chat: Chat): boolean {
  return chat.kind === 'system_rules_activity' || chat.kind === 'rule_activity'
}

type Bucket = 'Today' | 'Yesterday' | 'Previous 7 days' | 'Previous 30 days' | 'Older'

const BUCKET_ORDER: Bucket[] = [
  'Today',
  'Yesterday',
  'Previous 7 days',
  'Previous 30 days',
  'Older',
]

const BUCKET_KEY: Record<Bucket, TKey> = {
  'Today': 'sidebar.groupToday',
  'Yesterday': 'sidebar.groupYesterday',
  'Previous 7 days': 'sidebar.groupPrev7',
  'Previous 30 days': 'sidebar.groupPrev30',
  'Older': 'sidebar.groupOlder',
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

function bucketFor(iso: string | null | undefined): Bucket {
  if (!iso) return 'Older'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return 'Older'
  const today = startOfDay(new Date())
  const oneDay = 86_400_000
  if (t >= today) return 'Today'
  if (t >= today - oneDay) return 'Yesterday'
  if (t >= today - 7 * oneDay) return 'Previous 7 days'
  if (t >= today - 30 * oneDay) return 'Previous 30 days'
  return 'Older'
}

function groupChats(chats: Chat[]): { bucket: Bucket; chats: Chat[] }[] {
  const map = new Map<Bucket, Chat[]>()
  for (const c of chats) {
    const b = bucketFor(c.updatedAt ?? c.createdAt)
    const arr = map.get(b)
    if (arr) arr.push(c)
    else map.set(b, [c])
  }
  return BUCKET_ORDER.flatMap((b) => {
    const list = map.get(b)
    return list && list.length ? [{ bucket: b, chats: list }] : []
  })
}

type ChatRowProps = {
  chat: Chat
  active: boolean
  onRename: (chat: Chat) => void
  onDelete: (chat: Chat) => void
  onExport: (chat: Chat, format: ExportFormat) => void
}

function ChatRow({ chat, active, onRename, onDelete, onExport }: ChatRowProps) {
  const { t, lang } = useT()
  const [menuOpen, setMenuOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const closeMenu = () => {
    setMenuOpen(false)
    setExportOpen(false)
  }
  const ts = chat.updatedAt ?? chat.createdAt
  const title = chat.title || t('sidebar.newChat').replace(/^\+\s*/, '')
  return (
    <div
      data-active={active}
      className={clsx(
        'glass-row group relative flex items-center gap-2 rounded-md ps-3 pe-1.5 py-1.5 text-[13px] cursor-pointer',
        active
          ? 'text-neutral-50 bg-white/[0.07] ring-1 ring-white/10'
          : 'text-neutral-300 hover:text-neutral-100',
      )}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenuOpen((o) => {
          if (o) setExportOpen(false)
          return !o
        })
      }}
    >
      {active && (
        <span
          aria-hidden
          className="absolute start-0 top-1/2 h-5 w-[2px] -translate-y-1/2 rounded-e bg-blue-400/80"
        />
      )}
      <Link
        to={`/chat/${chat.id}`}
        className="flex min-w-0 flex-1 items-center gap-2"
        onClick={closeMenu}
        title={title}
      >
        <span dir="auto" className="truncate flex-1">
          {title}
        </span>
        <span
          className={clsx(
            'shrink-0 text-[10px] tabular-nums text-neutral-500 transition-opacity',
            menuOpen ? 'opacity-0' : 'group-hover:opacity-0',
          )}
        >
          {formatRelative(ts, t, lang)}
        </span>
      </Link>
      <button
        type="button"
        aria-label={t('sidebar.chatActions')}
        className={clsx(
          'absolute end-1.5 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-neutral-400 hover:text-neutral-100 hover:bg-white/10 transition-opacity',
          menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
        )}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setMenuOpen((o) => {
            if (o) setExportOpen(false)
            return !o
          })
        }}
      >
        ⋯
      </button>
      {menuOpen && (
        <div
          className="glass-elevated absolute end-1 top-full z-10 mt-1 w-36 rounded-lg overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="block w-full px-3 py-2 text-start text-sm text-neutral-200 hover:bg-white/10"
            onClick={() => {
              closeMenu()
              onRename(chat)
            }}
          >
            {t('common.rename')}
          </button>
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={exportOpen}
            className="flex w-full items-center justify-between px-3 py-2 text-start text-sm text-neutral-200 hover:bg-white/10"
            onClick={() => setExportOpen((o) => !o)}
          >
            <span>{t('sidebar.export')}</span>
            <span aria-hidden className="text-[10px] text-neutral-500">
              {exportOpen ? '▾' : '▸'}
            </span>
          </button>
          {exportOpen && (
            <div className="border-y border-white/5 bg-white/[0.03]">
              <button
                type="button"
                className="block w-full px-5 py-2 text-start text-sm text-neutral-200 hover:bg-white/10"
                onClick={() => {
                  closeMenu()
                  onExport(chat, 'markdown')
                }}
              >
                {t('sidebar.exportMarkdown')}
              </button>
              <button
                type="button"
                className="block w-full px-5 py-2 text-start text-sm text-neutral-200 hover:bg-white/10"
                onClick={() => {
                  closeMenu()
                  onExport(chat, 'json')
                }}
              >
                {t('sidebar.exportJson')}
              </button>
            </div>
          )}
          <button
            type="button"
            className="block w-full px-3 py-2 text-start text-sm text-red-300 hover:bg-white/10"
            onClick={() => {
              closeMenu()
              onDelete(chat)
            }}
          >
            {t('common.delete')}
          </button>
        </div>
      )}
    </div>
  )
}

export default function Sidebar() {
  const { t } = useT()
  // Sidebar renders outside <Routes>, so useParams() is always empty here.
  const chatMatch = useMatch('/chat/:id')
  const activeId = chatMatch?.params.id ? Number(chatMatch.params.id) : null
  const navigate = useNavigate()
  const qc = useQueryClient()
  const location = useLocation()
  const pushToast = useAppStore((s) => s.pushToast)

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
    onError: (err) =>
      pushToast({
        kind: 'error',
        text: t('toast.renameFailed', { error: (err as Error).message }),
      }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteChat(id),
    onSuccess: async (_, id) => {
      await qc.invalidateQueries({ queryKey: ['chats'] })
      if (id === activeId) navigate('/', { replace: true })
    },
    onError: (err) =>
      pushToast({
        kind: 'error',
        text: t('toast.deleteFailed', { error: (err as Error).message }),
      }),
  })

  const handleRename = (chat: Chat) => {
    const next = window.prompt(t('common.rename'), chat.title ?? '')
    if (next && next.trim() && next !== chat.title) {
      renameMut.mutate({ id: chat.id, title: next.trim() })
    }
  }

  const handleDelete = (chat: Chat) => {
    const fallback = t('sidebar.newChat').replace(/^\+\s*/, '')
    if (window.confirm(`${t('common.delete')} "${chat.title || fallback}"?`)) {
      deleteMut.mutate(chat.id)
    }
  }

  const handleExport = (chat: Chat, format: ExportFormat) => {
    // Pure client-side: fetch messages, build a Blob, trigger a download. Any
    // failure (network / serialization) surfaces as a toast rather than throwing.
    void exportChat(chat, format).catch((err) =>
      pushToast({
        kind: 'error',
        text: t('sidebar.exportFailed', { error: (err as Error).message }),
      }),
    )
  }

  const rulesActive = location.pathname.startsWith('/rules')

  return (
    <div className="flex h-full flex-col">
      <div className="p-3 border-b border-white/5">
        <div className="flex items-center gap-2.5 mb-3 px-1">
          <span
            aria-hidden
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-teal-400 text-white shadow-[0_4px_16px_rgba(99,102,241,0.45)]"
          >
            <svg viewBox="0 0 16 16" fill="currentColor" className="h-4 w-4">
              <path d="M6.2 11.6 2.9 8.3l1.2-1.2 2.1 2.1 5.7-5.7 1.2 1.2-6.9 6.9Z" />
            </svg>
          </span>
          <h1 className="text-[15px] font-bold text-neutral-50">
            {t('sidebar.appTitle')}
          </h1>
        </div>
        <button
          type="button"
          className="glass-input w-full rounded-lg px-3 py-2 text-sm text-neutral-100 hover:bg-white/10 disabled:opacity-50 transition"
          disabled={createMut.isPending}
          onClick={() => createMut.mutate()}
        >
          {t('sidebar.newChat')}
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
            <div className="truncate text-sm">{t('sidebar.rules')}</div>
            <div className="text-[11px] text-neutral-500">
              {t('sidebar.rulesSubtitle')}
            </div>
          </div>
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {groupChats(regularChats).map((group, i) => (
          <div key={group.bucket} className="space-y-0.5">
            <div
              className={clsx(
                'px-2 pb-1 text-[11px] font-medium text-neutral-500',
                i === 0 ? 'pt-1' : 'pt-3',
              )}
            >
              {t(BUCKET_KEY[group.bucket])}
            </div>
            {group.chats.map((c) => (
              <ChatRow
                key={c.id}
                chat={c}
                active={c.id === activeId}
                onRename={handleRename}
                onDelete={handleDelete}
                onExport={handleExport}
              />
            ))}
          </div>
        ))}
        {(!chats || regularChats.length === 0) && (
          <div className="px-2 py-4 text-xs text-neutral-500">
            {t('sidebar.empty')}
          </div>
        )}
      </div>

      <div className="border-t border-white/5 p-3">
        <Link
          to="/settings"
          className="block rounded-lg px-3 py-2 text-sm text-neutral-300 hover:bg-white/10 transition"
        >
          {t('sidebar.settings')}
        </Link>
      </div>
    </div>
  )
}
