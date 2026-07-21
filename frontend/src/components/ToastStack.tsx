import type { Toast } from '../types'

const ICONS: Record<Toast['severity'], string> = {
  INFO: '◆',
  MEDIUM: '▲',
  HIGH: '●',
}

const CLASSES: Record<Toast['severity'], string> = {
  INFO: 'toast-info',
  MEDIUM: 'toast-medium',
  HIGH: 'toast-high',
}

import { useToastStore } from '../stores/toasts'

export function ToastStack() {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)

  return (
    <div className="toast-stack">
      {toasts.slice(-4).map((toast: Toast, index: number) => (
        <div
          key={toast.id}
          className={`toast ${CLASSES[toast.severity]}${toast.leaving ? ' leaving' : ''}`}
          style={{ '--stagger': index } as React.CSSProperties}
        >
          <span className="toast-icon">{ICONS[toast.severity]}</span>
          <div>
            <div className="toast-title">{toast.title}</div>
            <div className="toast-body">{toast.message}</div>
            {toast.meta && <div className="toast-meta">{toast.meta}</div>}
          </div>
          <button className="toast-dismiss" onClick={() => dismiss(toast.id)} aria-label="Dismiss">
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}
