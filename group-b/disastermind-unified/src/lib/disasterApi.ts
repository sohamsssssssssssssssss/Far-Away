const API_BASE_URL = 'http://localhost:8000'
const WS_URL = 'ws://localhost:8000/ws'

export interface Message {
  id: string
  sender: string
  recipient: string
  type: 'alert' | 'instruction' | 'query' | 'acknowledgement' | 'escalation' | string
  priority: 1 | 2 | 3 | 4 | 5 | number
  payload: Record<string, unknown>
  reasoning: string[]
  ttl_seconds: number
  topic: string
  incident_id: string
  module: string
  escalation_trigger: unknown
  timestamp: string
}

export interface Escalation {
  id: string
  message: Message
  status: 'pending' | 'approved' | 'rejected' | string
  created_at: string
  decision_required_by: string
}

export type WebSocketConnectionState = 'connected' | 'reconnecting' | 'offline'

export function connectWebSocket(
  onMessage: (msg: Message) => void,
  onStateChange?: (state: WebSocketConnectionState) => void,
): () => void {
  let socket: WebSocket | null = null
  let reconnectTimer: number | undefined
  let closedByClient = false

  const connect = () => {
    if (closedByClient) {
      return
    }

    onStateChange?.('reconnecting')

    try {
      socket = new WebSocket(WS_URL)
    } catch (error) {
      console.log('Group A WebSocket unavailable; using simulation mode.', error)
      onStateChange?.('offline')
      reconnectTimer = window.setTimeout(connect, 3000)
      return
    }

    socket.onopen = () => {
      onStateChange?.('connected')
    }

    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data) as Message)
      } catch (error) {
        console.log('Ignored malformed Group A WebSocket message.', error)
      }
    }

    socket.onerror = (error) => {
      console.log('Group A WebSocket unavailable; using simulation mode.', error)
      onStateChange?.('offline')
    }

    socket.onclose = () => {
      if (closedByClient) {
        return
      }

      onStateChange?.('offline')
      reconnectTimer = window.setTimeout(connect, 3000)
    }
  }

  connect()

  return () => {
    closedByClient = true
    if (reconnectTimer !== undefined) {
      window.clearTimeout(reconnectTimer)
    }
    socket?.close()
  }
}

export async function fetchEscalations(): Promise<Escalation[]> {
  try {
    const response = await fetch(`${API_BASE_URL}/escalations`)
    if (!response.ok) {
      return []
    }

    return await response.json() as Escalation[]
  } catch (error) {
    console.log('Group A escalations unavailable; using mock queue.', error)
    return []
  }
}

export async function approveEscalation(id: string): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/escalations/${id}/approve`, {
      method: 'POST',
    })
    return response.ok
  } catch (error) {
    console.log('Group A escalation approve failed.', error)
    return false
  }
}

export async function rejectEscalation(id: string, reason: string): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/escalations/${id}/reject`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ reason }),
    })
    return response.ok
  } catch (error) {
    console.log('Group A escalation reject failed.', error)
    return false
  }
}

export async function checkBackendHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/health`)
    return response.ok
  } catch (error) {
    console.log('Group A backend health check failed; using simulation mode.', error)
    return false
  }
}
