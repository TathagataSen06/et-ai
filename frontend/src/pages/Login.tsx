import { useState } from 'react'
import { useAuthStore } from '../stores/auth'

export function Login() {
  const login = useAuthStore((s) => s.login)
  const [username, setUsername] = useState('commander')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <span className="brand-mark">◆</span>
          <span className="login-title">Project Netra</span>
        </div>
        <div className="login-sub">Counterfeit Currency Intelligence · Restricted Access</div>

        <label htmlFor="login-user">Username</label>
        <input
          id="login-user"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
        />

        <label htmlFor="login-pass">Password</label>
        <input
          id="login-pass"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          autoFocus
        />

        {error && <div className="login-error">{error}</div>}

        <button className="btn btn-primary login-btn" type="submit" disabled={busy || !password}>
          {busy ? 'Signing in…' : 'Sign In'}
        </button>

        <div className="login-hint">
          Demo credentials: <code>commander</code> / <code>netra-demo</code>
        </div>
      </form>
    </div>
  )
}
