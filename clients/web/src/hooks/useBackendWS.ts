import { useEffect, useRef, useState, useCallback } from 'react'
import { config } from '../lib/config'

export type WSConnectionState = 'connecting' | 'live' | 'reconnecting' | 'offline'

export interface BackendWSMessage {
  id: string
  topic: string
  type: 'alert' | 'instruction' | 'query' | 'acknowledgement' | 'escalation'
  priority: number         // 1 (critical) to 5 (info)
  module: 'A' | 'B' | 'C'
  incident_id: string
  escalation_trigger: string | null
  reasoning: string[]
  timestamp: string
  ttl_seconds: number
  sender: string
  recipient: string
  payload: Record<string, unknown>
}

const WS_URL = config.api.wsUrl

export function useBackendWS(onMessage: (msg: BackendWSMessage) => void) {
  const [connectionState, setConnectionState] = useState<WSConnectionState>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCount = useRef(0)
  const MAX_RETRIES = 5
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    setConnectionState(retryCount.current === 0 ? 'connecting' : 'reconnecting')

    try {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => {
        setConnectionState('live')
        retryCount.current = 0
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>

          // Skip snapshot and ping frames
          if (data.kind === 'snapshot' || data.kind === 'ping') return

          onMessageRef.current(data as unknown as BackendWSMessage)
        } catch {
          // malformed frame — ignore
        }
      }

      ws.onclose = () => {
        wsRef.current = null
        if (retryCount.current < MAX_RETRIES) {
          setConnectionState('reconnecting')
          const delay = Math.min(1000 * 2 ** retryCount.current, 30000)
          retryCount.current += 1
          retryRef.current = setTimeout(connect, delay)
        } else {
          setConnectionState('offline')
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    } catch {
      setConnectionState('offline')
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { connectionState }
}
