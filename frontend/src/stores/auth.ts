import { create } from 'zustand'

const STORAGE_KEY = 'netra.auth'

interface StoredAuth {
  token: string
  role: string
  username: string
}

function loadStored(): StoredAuth | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as StoredAuth) : null
  } catch {
    return null
  }
}

interface AuthState {
  token: string | null
  role: string | null
  username: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => {
  const stored = loadStored()
  return {
    token: stored?.token ?? null,
    role: stored?.role ?? null,
    username: stored?.username ?? null,

    login: async (username, password) => {
      const form = new URLSearchParams({ username, password })
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form,
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(detail?.detail ?? 'Login failed')
      }
      const body = await res.json()
      const auth: StoredAuth = {
        token: body.access_token,
        role: body.role,
        username: body.username,
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(auth))
      set(auth)
    },

    logout: () => {
      localStorage.removeItem(STORAGE_KEY)
      set({ token: null, role: null, username: null })
    },
  }
})

export function authHeaders(): Record<string, string> {
  const token = useAuthStore.getState().token
  return token ? { Authorization: `Bearer ${token}` } : {}
}
