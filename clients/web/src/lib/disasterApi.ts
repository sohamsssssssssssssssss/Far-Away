import { config } from './config';

// ─── TYPES ────────────────────────────────────────────────────────────────────

export type MessageType =
  | 'alert'
  | 'instruction'
  | 'query'
  | 'acknowledgement'
  | 'escalation';

export type MessagePriority = 1 | 2 | 3 | 4 | 5;
// 1 = CRITICAL, 2 = HIGH, 3 = MEDIUM, 4 = LOW, 5 = INFO

export type DisasterModule = 'A' | 'B' | 'C' | 'ALL';

export interface AgentMessage {
  id: string;
  sender: string;
  recipient: string;
  type: MessageType;
  priority: MessagePriority;
  payload: Record<string, unknown>;
  reasoning: string[];
  ttl_seconds: number;
  topic: string;
  incident_id: string;
  module: DisasterModule;
  escalation_trigger: string | null;
  timestamp: string;
}

export type EscalationStatus = 'pending' | 'approved' | 'rejected';

export interface Escalation {
  id: string;
  message: AgentMessage;
  status: EscalationStatus;
  created_at: string;
  decision_required_by: string;
}

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'down';
  timestamp: string;
  agents_active?: number;
  messages_processed?: number;
}

export interface TopicStats {
  topic: string;
  message_count: number;
  last_message_at: string;
}

// ─── HTTP HELPERS ─────────────────────────────────────────────────────────────

const BASE = config.api.baseUrl;

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json() as T;
  } catch (err) {
    console.warn(`[API] GET ${path} failed:`, err);
    return null;
  }
}

async function post<T>(path: string, body?: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json() as T;
  } catch (err) {
    console.warn(`[API] POST ${path} failed:`, err);
    return null;
  }
}

// ─── API METHODS ──────────────────────────────────────────────────────────────

export const disasterApi = {

  /** Check if Group A backend is reachable */
  async health(): Promise<HealthStatus | null> {
    return get<HealthStatus>('/health');
  },

  /** Get active message counts per topic */
  async topics(): Promise<TopicStats[] | null> {
    return get<TopicStats[]>('/topics');
  },

  /** Get recent message bus entries */
  async incidents(limit = 50): Promise<AgentMessage[] | null> {
    return get<AgentMessage[]>(`/incidents?limit=${limit}`);
  },

  /** Get all pending escalations */
  async escalations(): Promise<Escalation[] | null> {
    return get<Escalation[]>('/escalations');
  },

  /** Approve an escalation */
  async approveEscalation(id: string): Promise<boolean> {
    const res = await post(`/escalations/${id}/approve`);
    return res !== null;
  },

  /** Reject an escalation with a reason */
  async rejectEscalation(id: string, reason: string): Promise<boolean> {
    const res = await post(`/escalations/${id}/reject`, { reason });
    return res !== null;
  },
};

// ─── WEBSOCKET CLIENT ─────────────────────────────────────────────────────────

export type WSConnectionState =
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'offline';

export interface WSClient {
  connectionState: WSConnectionState;
  disconnect: () => void;
}

/**
 * Connect to Group A's WebSocket stream.
 * Auto-reconnects every 3 seconds on disconnect.
 * Returns a cleanup function — call it on component unmount.
 *
 * Usage:
 *   const cleanup = connectWebSocket(
 *     (msg) => console.log('new message', msg),
 *     (state) => setConnectionState(state)
 *   );
 *   return () => cleanup();
 */
export function connectWebSocket(
  onMessage: (msg: AgentMessage) => void,
  onStateChange?: (state: WSConnectionState) => void
): () => void {
  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let destroyed = false;

  function setState(state: WSConnectionState) {
    onStateChange?.(state);
  }

  function connect() {
    if (destroyed) return;

    setState('connecting');

    try {
      ws = new WebSocket(config.api.wsUrl);

      ws.onopen = () => {
        setState('connected');
        console.log('[WS] Connected to Group A');
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as AgentMessage;
          onMessage(msg);
        } catch (err) {
          console.warn('[WS] Failed to parse message:', err);
        }
      };

      ws.onclose = () => {
        if (destroyed) return;
        setState('reconnecting');
        console.log('[WS] Disconnected — reconnecting in 3s');
        reconnectTimer = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        setState('offline');
        ws?.close();
      };
    } catch {
      setState('offline');
      if (!destroyed) {
        reconnectTimer = setTimeout(connect, 3000);
      }
    }
  }

  connect();

  // Return cleanup function
  return () => {
    destroyed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    ws?.close();
    setState('offline');
  };
}

// ─── MESSAGE HELPERS ──────────────────────────────────────────────────────────

/** Convert agent sender name to display label
 *  "prediction_agent" → "PREDICTION-AI"
 *  "commander_agent"  → "COMMANDER-AI"
 */
export function formatAgentName(sender: string): string {
  if (!sender) return 'UNKNOWN-AI';
  return sender
    .replace(/_agent$/, '')
    .replace(/_/g, '-')
    .toUpperCase() + '-AI';
}

/** Convert priority number to severity string */
export function priorityToSeverity(
  priority: MessagePriority
): 'critical' | 'high' | 'medium' | 'low' | 'info' {
  const map: Record<MessagePriority, 'critical' | 'high' | 'medium' | 'low' | 'info'> = {
    1: 'critical',
    2: 'high',
    3: 'medium',
    4: 'low',
    5: 'info',
  };
  return map[priority] ?? 'info';
}

/** Extract display text from a message payload */
export function extractMessageText(msg: AgentMessage): string {
  const p = msg.payload ?? {};
  if (typeof p.summary === 'string') return p.summary;
  if (typeof p.action === 'string') return p.action;
  if (typeof p.description === 'string') return p.description;
  if (msg.reasoning && msg.reasoning.length > 0) return msg.reasoning[0];
  return `${formatAgentName(msg.sender)} decision on ${msg.topic ?? 'unknown'}`;
}
