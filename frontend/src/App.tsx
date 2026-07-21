import { useState } from 'react'
import { ToastStack } from './components/ToastStack'
import { CitizenReportPage } from './pages/CitizenReportPage'
import { CommandCenter } from './pages/CommandCenter'
import { Login } from './pages/Login'
import { NetworkGraphPage } from './pages/NetworkGraph'
import { Scanner } from './pages/Scanner'
import { useAuthStore } from './stores/auth'

type Page = 'command' | 'network' | 'scanner' | 'report'

const PAGES: { id: Page; label: string }[] = [
  { id: 'command', label: 'Command Center' },
  { id: 'network', label: 'Network' },
  { id: 'scanner', label: 'Scanner' },
  { id: 'report', label: 'Report' },
]

export default function App() {
  const [page, setPage] = useState<Page>('command')
  const token = useAuthStore((s) => s.token)
  const username = useAuthStore((s) => s.username)
  const logout = useAuthStore((s) => s.logout)

  if (!token) {
    return (
      <div className="app">
        <Login />
        <ToastStack />
      </div>
    )
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-name">
            <span className="brand-mark">◆</span>Project Netra
          </span>
          <span className="brand-sub">Counterfeit Currency Intelligence</span>
        </div>
        <nav className="topbar-nav">
          {PAGES.map((p) => (
            <button
              key={p.id}
              className={page === p.id ? 'nav-btn active' : 'nav-btn'}
              onClick={() => setPage(p.id)}
            >
              {p.label}
            </button>
          ))}
          <span className="topbar-user mono">{username}</span>
          <button className="btn btn-danger nav-logout" onClick={logout}>
            Sign Out
          </button>
        </nav>
      </header>
      <main className="content">
        {page === 'command' && <CommandCenter />}
        {page === 'network' && <NetworkGraphPage />}
        {page === 'scanner' && <Scanner />}
        {page === 'report' && <CitizenReportPage />}
      </main>
      <ToastStack />
    </div>
  )
}
