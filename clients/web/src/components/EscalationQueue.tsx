import { useMemo, useState } from 'react'
import { Check, X } from 'lucide-react'

type EscalationState = 'pending' | 'approved' | 'overridden' | 'hidden'

const escalations = [
  {
    id: 'evac-zone-7',
    title: 'MANDATORY EVACUATION - ZONE 7',
    situation: '14,200 residents in high inundation risk zone.',
    recommended: 'Issue mandatory evacuation order immediately.',
    countdown: '04:12',
  },
  {
    id: 'boat-request',
    title: 'CROSS-STATE RESOURCE REQUEST',
    situation: 'Boat deficit of 8 units projected within 2 hours.',
    recommended: 'Request 8 NDRF boats from Andhra Pradesh sector.',
    countdown: '02:47',
  },
]

export function EscalationQueue() {
  const [states, setStates] = useState<Record<string, EscalationState>>({})
  const pendingCount = useMemo(
    () => escalations.filter((item) => (states[item.id] ?? 'pending') === 'pending').length,
    [states],
  )

  const resolveEscalation = (id: string, next: 'approved' | 'overridden') => {
    setStates((current) => ({ ...current, [id]: next }))
    window.setTimeout(() => {
      setStates((current) => ({ ...current, [id]: 'hidden' }))
    }, 1500)
  }

  return (
    <section className="panel escalation-panel">
      <div className="panel-title">
        <h2>ESCALATION QUEUE</h2>
        <span className="count-badge">{pendingCount} PENDING</span>
      </div>
      <div className="escalation-list">
        {escalations.map((item) => {
          const state = states[item.id] ?? 'pending'
          if (state === 'hidden') {
            return null
          }
          const isResolved = state !== 'pending'
          return (
            <article className={`escalation-card ${state} ${isResolved ? 'resolved' : ''}`} key={item.id}>
              <div className="escalation-head">
                <h3>{item.title}</h3>
                <span className="timer">{item.countdown}</span>
              </div>
              {isResolved ? (
                <div className="resolution-state">
                  {state === 'approved' ? <Check size={20} /> : <X size={20} />}
                  {state === 'approved' ? 'APPROVED' : 'OVERRIDDEN'}
                </div>
              ) : (
                <>
                  <p>{item.situation}</p>
                  <p className="recommendation">Recommended: {item.recommended}</p>
                  <div className="escalation-actions">
                    <button type="button" className="approve-btn" onClick={() => resolveEscalation(item.id, 'approved')}>
                      APPROVE
                    </button>
                    <button type="button" className="override-btn" onClick={() => resolveEscalation(item.id, 'overridden')}>
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
