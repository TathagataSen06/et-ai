import { useEffect, useRef, useState } from 'react'
import type { LiveAlert } from '../types'

/** Connects to the dashboard alert stream with automatic reconnect. */
export function useDashboardSocket(onMessage: (msg: LiveAlert) => void) {
  const [connected, setConnected] = useState(false)
  const handlerRef = useRef(onMessage)
  handlerRef.current = onMessage

  useEffect(() => {
    let ws: WebSocket | null = null
    let retryTimer: number | undefined
    let closed = false

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${protocol}://${window.location.host}/ws/dashboard`)
      ws.onopen = () => setConnected(true)
      ws.onmessage = (event) => {
        try {
          handlerRef.current(JSON.parse(event.data))
        } catch {
          // ignore malformed frames
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retryTimer = window.setTimeout(connect, 3000)
      }
      ws.onerror = () => ws?.close()
    }

    connect()
    return () => {
      closed = true
      window.clearTimeout(retryTimer)
      ws?.close()
    }
  }, [])

  return connected
}
