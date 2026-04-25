import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import Sidebar from './components/Sidebar'
import Chat from './pages/Chat'
import { listChats, createChat } from './lib/api'
import { useAppStore } from './store'

// Off the critical path: lazy-load secondary pages so the initial chat
// bundle stays lean. Chat is the landing route and stays eager.
const Rules = lazy(() => import('./pages/Rules'))
const RuleActivity = lazy(() => import('./pages/RuleActivity'))
const Settings = lazy(() => import('./pages/Settings'))

function RootRedirect() {
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
      Loading…
    </div>
  )
}

function NotFound() {
  return (
    <div className="flex h-full items-center justify-center text-neutral-500">
      Not found.{' '}
      <Navigate to="/" replace />
    </div>
  )
}

export default function App() {
  const setChats = useAppStore((s) => s.setChats)
  const { data: chats } = useQuery({
    queryKey: ['chats'],
    queryFn: listChats,
  })

  useEffect(() => {
    if (chats) setChats(chats)
  }, [chats, setChats])

  return (
    <div className="flex h-full w-full text-neutral-200">
      <aside className="glass-panel w-64 shrink-0 border-y-0 border-l-0 rounded-none">
        <Sidebar />
      </aside>
      <main className="flex-1 min-w-0 flex flex-col">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center text-neutral-500">
              Loading…
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
    </div>
  )
}
