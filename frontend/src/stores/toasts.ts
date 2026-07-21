import { create } from 'zustand'
import type { Toast } from '../types'

const TOAST_TTL_MS = 8000
const TOAST_EXIT_MS = 220

let seq = 0

interface ToastState {
  toasts: Toast[]
  push: (toast: Omit<Toast, 'id'>) => void
  dismiss: (id: number) => void
}

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],

  push: (toast) => {
    const id = ++seq
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }] }))
    window.setTimeout(() => get().dismiss(id), TOAST_TTL_MS)
  },

  dismiss: (id) => {
    set((state) => ({
      toasts: state.toasts.map((t) => (t.id === id ? { ...t, leaving: true } : t)),
    }))
    window.setTimeout(
      () => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
      TOAST_EXIT_MS,
    )
  },
}))
