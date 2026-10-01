import { lazy, Suspense, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import Sidebar from './components/Sidebar'
import Toaster from './components/Toaster'
import Chat from './pages/Chat'
import { listChats, createChat, getSettings } from './lib/api'
import { useAppStore } from './store'
import { applyLanguage } from './i18n/applyLanguage'
import { useT } from './i18n/useT'

// Off the critical path: lazy-load secondary pages so the initial chat
// bundle stays lean. Chat is the landing route and stays eager.
const Rules = lazy(() => import('./pages/Rules'))
const RuleActivity = lazy(() => import('./pages/RuleActivity'))
const Settings = lazy(() => import('./pages/Settings'))

function RootRedirect() {
  const { t } = useT()
  const navigate = useNavigate()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['chats'],
    queryFn: listChats,
  })

  useEffect(() => {
    if (isLoading || isError) return
    const chats = data ?? []
    if (chats.length > 0) {
      navigate(`/chat/${chats[0].id}`, { replace: true })
      return
    }
    let cancelled = false
    createChat()
      .then((c) => {
        if (!cancelled) navigate(`/chat/${c.id}`, { replace: true })
      })
      .catch(() => {
        // swallow; user can retry via sidebar
      })
    return () => {
      cancelled = true
    }
  }, [data, isLoading, isError, navigate])

  return (
    <div className="flex h-full items-center justify-center text-neutral-500">
      {t('common.loading')}
    </div>
  )
}

function NotFound() {
  const { t } = useT()
  return (
    <div className="flex h-full items-center justify-center text-neutral-500">
      {t('common.notFound')}{' '}
      <Navigate to="/" replace />
    </div>
  )
}

export default function App() {
  const setChats = useAppStore((s) => s.setChats)
  const setSettings = useAppStore((s) => s.setSettings)
  const language = useAppStore((s) => s.settings?.language ?? 'fa')
  const { t } = useT()

  const { data: chats } = useQuery({
    queryKey: ['chats'],
    queryFn: listChats,
  })

  // Hydrate settings (incl. language) once on mount so applyLanguage can sync
  // <html lang/dir> to the user's stored preference. Settings page also calls
  // setSettings on save, so this only seeds the initial value.
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  })

  useEffect(() => {
    if (chats) setChats(chats)
  }, [chats, setChats])

  useEffect(() => {
    if (settings) setSettings(settings)
  }, [settings, setSettings])

  useEffect(() => {
    applyLanguage(language)
  }, [language])

  // Mobile drawer: remember which path it was opened on, so navigating
  // anywhere closes it without an effect.
  const { pathname } = useLocation()
  const [navOpenFor, setNavOpenFor] = useState<string | null>(null)
  const navOpen = navOpenFor === pathname

  return (
    <div className="flex h-full w-full text-neutral-200">
      {navOpen && (
        <button
          type="button"
          aria-label={t('sidebar.closeMenu')}
          className="glass-backdrop fixed inset-0 z-30 md:hidden"
          onClick={() => setNavOpenFor(null)}
        />
      )}
      <aside
        className={clsx(
          'glass-panel w-64 shrink-0 border-y-0 border-s-0 rounded-none md:static md:block',
          navOpen ? 'fixed inset-y-0 start-0 z-40 block' : 'hidden',
        )}
      >
        <Sidebar />
      </aside>
      <main className="flex-1 min-w-0 flex flex-col">
        <div className="flex items-center gap-3 border-b border-white/5 px-4 py-2.5 md:hidden">
          <button
            type="button"
            aria-label={t('sidebar.openMenu')}
            className="glass-input rounded-lg p-2 text-neutral-200"
            onClick={() => setNavOpenFor(pathname)}
          >
            <svg viewBox="0 0 16 16" fill="currentColor" className="h-4 w-4" aria-hidden>
              <path d="M2 4h12v1.5H2zM2 7.25h12v1.5H2zM2 10.5h12V12H2z" />
            </svg>
          </button>
          <span className="text-sm font-semibold text-neutral-100">
            {t('sidebar.appTitle')}
          </span>
        </div>
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center text-neutral-500">
              {t('common.loading')}
            </div>
          }
        >
          <Routes>
            <Route path="/" element={<RootRedirect />} />
            <Route path="/chat/:id" element={<Chat />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="/rules/:id/activity" element={<RuleActivity />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </main>
      <Toaster />
    </div>
  )
}
