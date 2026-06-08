import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, X } from 'lucide-react'
import {
  approveEscalation,
  fetchEscalations,
  rejectEscalation,
  type Escalation,
  type Message,
} from '../../../lib/disasterApi'

type EscalationState = 'pending' | 'approved' | 'overridden' | 'hidden'
type QueueItem = {
  id: string
  title: string
  situation: string
  recommended: string
  decisionRequiredBy: string
  source: 'mock' | 'backend'
}

type EscalationQueueProps = {
  backendOnline: boolean
  incomingMessage: Message | null
  timelineEscalations?: QueueItem[]
  onApproveZone7?: () => void
  zone7OverrideState?: 'pending' | 'auto-executing' | 'approved' | 'overridden' | 'removed'
}

const escalations = [
  {
    id: 'evac-zone-7',
    title: 'MANDATORY EVACUATION - ZONE 7',
    situation: '14,200 residents in high inundation risk zone.',
    recommended: 'Issue mandatory evacuation order immediately.',
    decisionRequiredBy: new Date(Date.now() + 252000).toISOString(),
    source: 'mock',
  },
  {
    id: 'boat-request',
    title: 'CROSS-STATE RESOURCE REQUEST',
    situation: 'Boat deficit of 8 units projected within 2 hours.',
    recommended: 'Request 8 NDRF boats from Andhra Pradesh sector.',
    decisionRequiredBy: new Date(Date.now() + 167000).toISOString(),
    source: 'mock',
  },
] satisfies QueueItem[]

function payloadText(payload: Record<string, unknown>, keys: string[], fallback: string) {
  for (const key of keys) {
    const value = payload[key]
    if (typeof value === 'string' && value.trim()) {
      return value
    }
  }

  return fallback
}

function escalationIdFromMessage(message: Message) {
  const payloadId = message.payload.escalation_id ?? message.payload.escalationId ?? message.payload.id
  if (typeof payloadId === 'string' && payloadId.trim()) {
    return payloadId
  }

  if (typeof message.escalation_trigger === 'string' && message.escalation_trigger.trim()) {
    return message.escalation_trigger
  }

  return message.id
}

function messageToQueueItem(message: Message, decisionRequiredBy?: string): QueueItem {
  const fallbackSummary = message.reasoning[0] ?? 'Group A escalation requires commander review.'
  const title = payloadText(message.payload, ['title', 'summary', 'action'], 'GROUP A ESCALATION')
  const situation = payloadText(message.payload, ['situation', 'summary', 'description'], fallbackSummary)
  const recommended = payloadText(message.payload, ['recommended', 'recommendation', 'action'], fallbackSummary)
  const createdAt = new Date(message.timestamp)
  const fallbackDeadline = new Date(
    Number.isNaN(createdAt.getTime()) ? Date.now() + 300000 : createdAt.getTime() + 300000,
  ).toISOString()

  return {
    id: escalationIdFromMessage(message),
    title: title.toUpperCase(),
    situation,
    recommended,
    decisionRequiredBy: decisionRequiredBy ?? fallbackDeadline,
    source: 'backend',
  }
}

function escalationToQueueItem(escalation: Escalation): QueueItem {
  return messageToQueueItem(escalation.message, escalation.decision_required_by)
}

function formatCountdown(deadline: string, now: number) {
  const target = new Date(deadline).getTime()
  const remainingSeconds = Math.max(0, Math.floor((target - now) / 1000))
  const minutes = Math.floor(remainingSeconds / 60).toString().padStart(2, '0')
  const seconds = (remainingSeconds % 60).toString().padStart(2, '0')

  return `${minutes}:${seconds}`
}

export function EscalationQueue({
  backendOnline,
  incomingMessage,
  timelineEscalations = [],
  onApproveZone7,
  zone7OverrideState,
}: EscalationQueueProps) {
  const [states, setStates] = useState<Record<string, EscalationState>>({})
  const [backendEscalations, setBackendEscalations] = useState<QueueItem[]>([])
  const [now, setNow] = useState(Date.now())
  const lastMessageIdRef = useRef<string | null>(null)
  const activeEscalations = backendOnline
    ? backendEscalations
    : [...timelineEscalations, ...escalations]
  const pendingCount = useMemo(
    () => activeEscalations.filter((item) => (states[item.id] ?? 'pending') === 'pending').length,
    [activeEscalations, states],
  )

  useEffect(() => {
    const tickTimer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(tickTimer)
  }, [])

  useEffect(() => {
    let cancelled = false

    const loadEscalations = async () => {
      const pendingEscalations = (await fetchEscalations())
        .filter((item) => item.status === 'pending')
        .map(escalationToQueueItem)

      if (!cancelled) {
        setBackendEscalations(pendingEscalations)
      }
    }

    loadEscalations()

    if (!backendOnline) {
      return () => {
        cancelled = true
      }
    }

    const pollTimer = window.setInterval(loadEscalations, 30000)

    return () => {
      cancelled = true
      window.clearInterval(pollTimer)
    }
  }, [backendOnline])

  useEffect(() => {
    if (
      !incomingMessage ||
      incomingMessage.id === lastMessageIdRef.current ||
      (incomingMessage.type !== 'escalation' && incomingMessage.escalation_trigger === null)
    ) {
      return
    }

    lastMessageIdRef.current = incomingMessage.id
    const nextEscalation = messageToQueueItem(incomingMessage)

    setBackendEscalations((current) => {
      if (current.some((item) => item.id === nextEscalation.id)) {
        return current
      }

      return [nextEscalation, ...current]
    })
  }, [incomingMessage])

  const resolveEscalation = (id: string, next: 'approved' | 'overridden') => {
    setStates((current) => ({ ...current, [id]: next }))
    window.setTimeout(() => {
      setStates((current) => ({ ...current, [id]: 'hidden' }))
      setBackendEscalations((current) => current.filter((item) => item.id !== id))
    }, 1500)
  }

  const approveItem = async (item: QueueItem) => {
    if (item.id === 'evac-zone-7-escalation') {
      onApproveZone7?.()
    }
    if (!backendOnline || item.source === 'mock') {
      resolveEscalation(item.id, 'approved')
      return
    }

    if (await approveEscalation(item.id)) {
      resolveEscalation(item.id, 'approved')
    }
  }

  const overrideItem = async (item: QueueItem) => {
    if (!backendOnline || item.source === 'mock') {
      resolveEscalation(item.id, 'overridden')
      return
    }

    const reason = window.prompt('Override reason')
    if (reason === null) {
      return
    }

    if (await rejectEscalation(item.id, reason)) {
      resolveEscalation(item.id, 'overridden')
    }
  }

  return (
    <section className="panel escalation-panel">
      <div className="panel-title">
        <h2>
          ESCALATION QUEUE
          <span
            style={{
              marginLeft: 8,
              color: backendOnline ? '#00e676' : '#7a8baa',
              font: '700 10px/1 var(--font-mono)',
              letterSpacing: '.08em',
            }}
          >
            {backendOnline ? 'LIVE' : 'MOCK'}
          </span>
        </h2>
        <span className="count-badge">{pendingCount} PENDING</span>
      </div>
      <style>{`
        @keyframes pulse-red-once-anim {
          0% { box-shadow: 0 0 0 0 rgba(255, 59, 59, 0.8); border-color: rgba(255, 59, 59, 0.8); }
          50% { box-shadow: 0 0 12px 6px rgba(255, 59, 59, 0.5); border-color: rgba(255, 59, 59, 0.6); }
          100% { box-shadow: 0 0 0 0 rgba(255, 59, 59, 0); }
        }
        .pulse-once {
          animation: pulse-red-once-anim 1.5s ease-out 1;
        }
      `}</style>
      <div className="escalation-list">
        {activeEscalations.map((item) => {
          const isZone7 = item.id === 'evac-zone-7-escalation'
          const isAutoExecuting = isZone7 && zone7OverrideState === 'auto-executing'
          const state = isAutoExecuting ? 'auto-executing' : (states[item.id] ?? 'pending')
          
          if (state === 'hidden') {
            return null
          }
          const isResolved = state !== 'pending' && state !== 'auto-executing'
          const cardTitle = isAutoExecuting
            ? '⚡ AUTO-EXECUTING — COMMANDER OVERRIDE WINDOW MISSED'
            : item.title

          return (
            <article
              className={`escalation-card ${state} ${isResolved ? 'resolved' : ''} ${isZone7 && state === 'pending' ? 'pulse-once' : ''}`}
              key={item.id}
              style={isAutoExecuting ? { background: '#ff3b3b', color: '#ffffff', borderColor: '#ff3b3b' } : undefined}
            >
              <div className="escalation-head">
                <h3 style={isAutoExecuting ? { color: '#ffffff' } : undefined}>{cardTitle}</h3>
                <span className="timer" style={isAutoExecuting ? { color: '#ffffff' } : undefined}>
                  {isAutoExecuting ? '00:00' : formatCountdown(item.decisionRequiredBy, now)}
                </span>
              </div>
              {isResolved ? (
                <div className="resolution-state">
                  {state === 'approved' ? <Check size={20} /> : <X size={20} />}
                  {state === 'approved' ? 'APPROVED' : 'OVERRIDDEN'}
                </div>
              ) : isAutoExecuting ? (
                <div style={{ marginTop: '8px', fontSize: '11px', fontWeight: 700, opacity: 0.9 }}>
                  Evacuation order is being auto-issued by command authority.
                </div>
              ) : (
                <>
                  <p style={isAutoExecuting ? { color: '#ffffff' } : undefined}>{item.situation}</p>
                  <p className="recommendation" style={isAutoExecuting ? { color: '#ffffff' } : undefined}>
                    Recommended: {item.recommended}
                  </p>
                  <div className="escalation-actions">
                    <button type="button" className="approve-btn" onClick={() => void approveItem(item)}>
                      APPROVE
                    </button>
                    <button type="button" className="override-btn" onClick={() => void overrideItem(item)}>
                      OVERRIDE
                    </button>
                  </div>
                </>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
