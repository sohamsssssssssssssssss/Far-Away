import { useEffect, useMemo, useRef, useState } from 'react'
import {
  disasterApi,
  type Escalation,
  type AgentMessage,
} from '../../../lib/disasterApi'
import { approveEscalation as backendApprove, rejectEscalation as backendReject } from '../../../services/backendService'
import { useEscalations } from '../../../hooks/useEscalations'
import { EscalationMemoCard } from '../../../components/EscalationMemoCard'

type EscalationState = 'pending' | 'approved' | 'overridden' | 'hidden'
type OldQueueItem = {
  id: string
  title: string
  situation: string
  recommended: string
  decisionRequiredBy: string
  source: 'mock' | 'backend'
}

type EscalationQueueProps = {
  backendOnline: boolean
  incomingMessage: AgentMessage | null
  timelineEscalations?: OldQueueItem[]
  onApproveZone7?: () => void
  zone7OverrideState?: 'pending' | 'auto-executing' | 'approved' | 'overridden' | 'removed'
}

const oldEscalations: OldQueueItem[] = [
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
]

function payloadText(payload: Record<string, unknown> | undefined, keys: string[], fallback: string) {
  if (!payload) return fallback
  for (const key of keys) {
    const value = payload[key]
    if (typeof value === 'string' && value.trim()) {
      return value
    }
  }
  return fallback
}

function escalationIdFromMessage(message: AgentMessage) {
  const p = message.payload ?? {}
  const payloadId = p.escalation_id ?? p.escalationId ?? p.id
  if (typeof payloadId === 'string' && payloadId.trim()) {
    return payloadId
  }
  if (typeof message.escalation_trigger === 'string' && message.escalation_trigger.trim()) {
    return message.escalation_trigger
  }
  return message.id
}

function messageToQueueItem(message: AgentMessage, decisionRequiredBy?: string): OldQueueItem {
  const fallbackSummary = message.reasoning?.[0] ?? 'Group A escalation requires commander review.'
  const p = message.payload ?? {}
  const title = payloadText(p, ['title', 'summary', 'action'], 'GROUP A ESCALATION')
  const situation = payloadText(p, ['situation', 'summary', 'description'], fallbackSummary)
  const recommended = payloadText(p, ['recommended', 'recommendation', 'action'], fallbackSummary)
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

function escalationToQueueItem(escalation: Escalation): OldQueueItem {
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
  const { pending, resolved, approve, overrideItem } = useEscalations()
  const [oldStates, setOldStates] = useState<Record<string, EscalationState>>({})
  const [backendEscalations, setBackendEscalations] = useState<OldQueueItem[]>([])
  const [now, setNow] = useState(Date.now())
  const lastMessageIdRef = useRef<string | null>(null)

  // 1-second tick for legacy countdowns
  useEffect(() => {
    const tickTimer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(tickTimer)
  }, [])

  // Backend polling (same as before)
  useEffect(() => {
    let cancelled = false
    const loadEscalations = async () => {
      const result = (await disasterApi.escalations()) ?? []
      const pendingEsc = result
        .filter((item) => item.status === 'pending')
        .map(escalationToQueueItem)
      if (!cancelled) {
        setBackendEscalations(pendingEsc)
      }
    }
    loadEscalations()
    if (!backendOnline) {
      return () => { cancelled = true }
    }
    const pollTimer = window.setInterval(loadEscalations, 30000)
    return () => {
      cancelled = true
      window.clearInterval(pollTimer)
    }
  }, [backendOnline])

  // Incoming WebSocket message → backend escalation
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

  const allLegacyItems: OldQueueItem[] = backendOnline
    ? backendEscalations
    : [...timelineEscalations, ...oldEscalations]

  // Count pending across all sources
  const pendingCount = useMemo(() => {
    const oldPending = allLegacyItems.filter(
      (item) => (oldStates[item.id] ?? 'pending') === 'pending'
    ).length
    return pending.length + oldPending
  }, [pending, allLegacyItems, oldStates])

  const hasCriticalPending = useMemo(() => {
    return pending.some(e => e.priority === 'CRITICAL')
  }, [pending])

  const resolveOldEscalation = (id: string, next: 'approved' | 'overridden') => {
    setOldStates((current) => ({ ...current, [id]: next }))
    window.setTimeout(() => {
      setOldStates((current) => ({ ...current, [id]: 'hidden' }))
      setBackendEscalations((current) => current.filter((item) => item.id !== id))
    }, 1500)
  }

  const handleApproveOldItem = async (item: OldQueueItem) => {
    if (item.id === 'evac-zone-7-escalation') {
      onApproveZone7?.()
    }
    void backendApprove(item.id)
    if (!backendOnline || item.source === 'mock') {
      resolveOldEscalation(item.id, 'approved')
      return
    }
    if (await disasterApi.approveEscalation(item.id)) {
      resolveOldEscalation(item.id, 'approved')
    }
  }

  const handleOverrideOldItem = async (item: OldQueueItem) => {
    if (!backendOnline || item.source === 'mock') {
      resolveOldEscalation(item.id, 'overridden')
      return
    }
    const reason = window.prompt('Override reason')
    if (reason === null) return
    void backendReject(item.id, 'CDR-SOHAM', reason)
    if (await disasterApi.rejectEscalation(item.id, reason)) {
      resolveOldEscalation(item.id, 'overridden')
    }
  }

  // Handle approve/override from EscalationMemoCard — also call demo callback if applicable
  const handleCardApprove = (id: string) => {
    if (id === 'evac-zone-7-escalation') {
      onApproveZone7?.()
    }
    approve(id)
    // Fire backend REST call in background
    void backendApprove(id)
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
        <span
          className="count-badge"
          style={{
            animation: hasCriticalPending ? 'pulse-red-once-anim 1.5s ease-out infinite' : undefined,
            borderColor: hasCriticalPending ? '#ef4444' : undefined,
            color: hasCriticalPending ? '#ef4444' : undefined,
          }}
        >
          {pendingCount} PENDING
        </span>
      </div>
      <style>{`
        @keyframes pulse-red-once-anim {
          0% { box-shadow: 0 0 0 0 rgba(255, 59, 59, 0.8); border-color: rgba(255, 59, 59, 0.8); }
          50% { box-shadow: 0 0 12px 6px rgba(255, 59, 59, 0.5); border-color: rgba(255, 59, 59, 0.6); }
          100% { box-shadow: 0 0 0 0 rgba(255, 59, 59, 0); }
        }
      `}</style>
      <div className="escalation-list" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {/* New-style pending cards from useEscalations */}
        {pending.map(item => (
          <EscalationMemoCard
            key={item.id}
            item={item}
            onApprove={handleCardApprove}
            onOverride={overrideItem}
          />
        ))}

        {/* Legacy timeline items (old format for demo compatibility) */}
        {allLegacyItems.map((item) => {
          const isZone7 = item.id === 'evac-zone-7-escalation'
          const isAutoExecuting = isZone7 && zone7OverrideState === 'auto-executing'
          const state = isAutoExecuting ? 'auto-executing' : (oldStates[item.id] ?? 'pending')

          if (state === 'hidden') return null
          const isResolved = state !== 'pending' && state !== 'auto-executing'
          const cardTitle = isAutoExecuting
            ? '⚡ AUTO-EXECUTING — COMMANDER OVERRIDE WINDOW MISSED'
            : item.title

          return (
            <article
              className={`escalation-card ${state} ${isResolved ? 'resolved' : ''} ${isZone7 && state === 'pending' ? 'pulse-once' : ''}`}
              key={item.id}
              style={{
                ...(isAutoExecuting ? { background: '#ff3b3b', color: '#ffffff', borderColor: '#ff3b3b' } : {}),
                ...(isResolved ? { minHeight: 'auto', padding: '8px 10px' } : {}),
              }}
            >
              <div className="escalation-head">
                <h3 style={isAutoExecuting ? { color: '#ffffff' } : undefined}>{cardTitle}</h3>
                <span className="timer" style={isAutoExecuting ? { color: '#ffffff' } : undefined}>
                  {isAutoExecuting ? '00:00' : formatCountdown(item.decisionRequiredBy, now)}
                </span>
              </div>
              {isResolved ? (
                <div className="resolution-state" style={{ fontSize: '16px', gap: '6px' }}>
                  {state === 'approved' ? '✓' : '✗'}
                  {' '}{state === 'approved' ? 'APPROVED' : 'OVERRIDDEN'}
                </div>
              ) : isAutoExecuting ? (
                <div style={{ marginTop: '4px', fontSize: '11px', fontWeight: 700, opacity: 0.9 }}>
                  Evacuation order is being auto-issued by command authority.
                </div>
              ) : (
                <>
                  <p style={isAutoExecuting ? { color: '#ffffff' } : undefined}>{item.situation}</p>
                  <p className="recommendation" style={isAutoExecuting ? { color: '#ffffff' } : undefined}>
                    Recommended: {item.recommended}
                  </p>
                  <div className="escalation-actions">
                    <button type="button" className="approve-btn" onClick={() => void handleApproveOldItem(item)}>
                      APPROVE
                    </button>
                    <button type="button" className="override-btn" onClick={() => void handleOverrideOldItem(item)}>
                      OVERRIDE
                    </button>
                  </div>
                </>
              )}
            </article>
          )
        })}

        {/* Resolved log from new-style escalations */}
        {resolved.length > 0 && (
          <div style={{
            borderTop: '1px solid rgba(58, 74, 107, 0.4)',
            paddingTop: '8px',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
          }}>
            <span style={{
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.08em',
              color: '#475569',
              padding: '0 2px',
            }}>
              RESOLVED
            </span>
            {resolved.map(item => (
              <EscalationMemoCard
                key={item.id}
                item={item}
                onApprove={() => {}}
                onOverride={() => {}}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
