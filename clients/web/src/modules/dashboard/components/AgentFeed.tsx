import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { formatAgentName, extractMessageText, priorityToSeverity } from '../../../lib/disasterApi'
import type { AgentMessage, WSConnectionState } from '../../../lib/disasterApi'
import { ShapBadges } from '../../../components/ShapBadges'
import { SYNTHETIC_SHAP, DEFAULT_SHAP } from '../../../lib/mapTypes'
import type { AgentDecisionShap } from '../../../lib/mapTypes'
import type { OverrideRecord } from '../../../lib/mapTypes'
import { OverridePanel } from '../../../components/OverridePanel'

type AgentEntry = {
  id: string
  time: string
  agent: string
  summary: string
  detail: string
  severity?: 'critical' | 'high' | 'medium' | 'low' | 'info'
  isNew?: boolean
  shap?: AgentDecisionShap
  overridden?: boolean
  overrideRecord?: OverrideRecord
}

type AgentFeedProps = {
  connectionState: WSConnectionState
  incomingMessage: AgentMessage | null
  customEntry?: {
    agent: string
    summary: string
    detail: string
    severity?: 'critical' | 'high' | 'medium' | 'low' | 'info'
  } | null
  onOverride?: (id: string, agentType: string, action: string, reason: string) => void
}

const initialEntries: AgentEntry[] = [
  {
    id: 'initial-064211',
    time: '06:42:11',
    agent: 'FLOOD-AI',
    summary: 'Inundation risk elevated to HIGH in Zone 7',
    detail: 'Mahanadi gauge telemetry rose faster than the 20-minute model band. Tide surge plus upstream release raises breach probability near Satapada.',
  },
  {
    id: 'initial-064158',
    time: '06:41:58',
    agent: 'RESOURCE-AI',
    summary: 'Rerouted 3 boats from Zone 4 to Zone 7',
    detail: 'Zone 4 demand dropped after shelter intake stabilized. Zone 7 projected deficit crosses the NDRF dispatch threshold within 38 minutes.',
  },
  {
    id: 'initial-064133',
    time: '06:41:33',
    agent: 'EVAC-AI',
    summary: 'Evacuation route ALPHA-3 marked congested',
    detail: 'Traffic speed fell below 12 km/h near Pipili junction. BETA-1 remains viable for light vehicles and buses from Zone 5.',
  },
  {
    id: 'initial-064055',
    time: '06:40:55',
    agent: 'COORD-AI',
    summary: 'TEAM-04 redirected to Balasore shelter',
    detail: 'Balasore School shelter requested triage support. TEAM-04 is closest with medical supplies and a clear route window.',
  },
  {
    id: 'initial-064012',
    time: '06:40:12',
    agent: 'FLOOD-AI',
    summary: 'River gauge Mahanadi: 91% danger level',
    detail: 'Gauge trend is accelerating with a 14-minute doubling in rise rate. Confidence is high after cross-checking CWC telemetry.',
  },
  {
    id: 'initial-063944',
    time: '06:39:44',
    agent: 'RESOURCE-AI',
    summary: 'Medical unit staged at Puri district HQ',
    detail: 'Puri shelter occupancy reached 73%. District HQ offers generator backup and ambulance access to both ALPHA and GAMMA corridors.',
  },
  {
    id: 'initial-063901',
    time: '06:39:01',
    agent: 'EVAC-AI',
    summary: 'Route BETA-1 activated for Zone 5',
    detail: 'BETA-1 has lower treefall exposure and passes two fuel points. Police barricade coordination sent to Khordha control.',
  },
  {
    id: 'initial-063830',
    time: '06:38:30',
    agent: 'COORD-AI',
    summary: 'Mutual aid request sent to Andhra Pradesh',
    detail: 'Boat inventory model forecasts an 8-unit shortfall. Request package includes staging at Ichchapuram and handoff to NDRF sector command.',
  },
]

const liveEntryPool: Omit<AgentEntry, 'id' | 'time' | 'isNew'>[] = [
  {
    agent: 'FLOOD-AI',
    summary: 'Mahanadi river gauge at 96% — danger threshold imminent',
    detail: 'Upstream gauge at Tikarapara rose 0.8m in 20 minutes. Catchment rainfall 214mm in 48h. Breach probability now 87% within 90 minutes.',
  },
  {
    agent: 'RESOURCE-AI',
    summary: 'Staging 4 additional boats at Ersama junction',
    detail: 'Zone 7 boat deficit projected in 45 minutes. Pre-positioning reduces response lag from 22 min to 8 min based on current traffic routing.',
  },
  {
    agent: 'EVAC-AI',
    summary: 'Route GAMMA-2 activated for Zone 6 overflow',
    detail: 'ALPHA-3 utilisation at 91%. Modelled throughput insufficient for projected Zone 6 outflow. GAMMA-2 adds 340 vehicles/hour capacity.',
  },
  {
    agent: 'COORD-AI',
    summary: 'TEAM-02 reassigned from Zone 5 to Zone 7',
    detail: 'Zone 5 cleared. Zone 7 survivor density estimated at 2.3x Zone 5. Optimal reallocation based on rescue capacity model.',
  },
  {
    agent: 'FLOOD-AI',
    summary: 'Inundation confirmed in Zone 7 sector B — 3 structures submerged',
    detail: 'Satellite SAR overlay + field report from TEAM-06 confirm water ingress. Affected structures: Chandpur primary school, 2 residential blocks.',
    severity: 'critical',
  },
  {
    agent: 'RESOURCE-AI',
    summary: 'Medical unit rerouted to Balasore shelter — 2 critical cases',
    detail: 'TEAM-06 flagged 2 critical survivors requiring immediate care. Nearest staged unit is MU-3 at Jajpur, ETA 14 minutes.',
  },
  {
    agent: 'EVAC-AI',
    summary: 'Contraflow activated on NH-16 between Balasore and Bhadrak',
    detail: 'Outbound evacuation volume 3.2x inbound relief traffic. Contraflow increases effective evacuation throughput by 60%.',
  },
  {
    agent: 'COORD-AI',
    summary: 'Mutual aid confirmed — 6 boats incoming from Andhra Pradesh NDRF',
    detail: 'Cross-state request approved by commander. ETA 2h 20min. Pre-assigned to Zone 7 sector C on arrival.',
  },
  {
    agent: 'FLOOD-AI',
    summary: 'River gauge Mahanadi: DANGER LEVEL BREACHED',
    detail: 'Gauge at Mundali crossed 49.83m danger mark at 16:34 IST. Downstream inundation model now projects Zone 8 impact within 3 hours.',
    severity: 'critical',
  },
  {
    agent: 'RESOURCE-AI',
    summary: 'Puri shelter at 94% — activating overflow to Bhubaneswar stadium',
    detail: 'Puri capacity 1,160. Current headcount 1,091 and rising. Bhubaneswar stadium pre-cleared as overflow with 2,400 capacity.',
  },
]

const agentClass: Record<string, string> = {
  'FLOOD-AI': 'cyan',
  'RESOURCE-AI': 'green',
  'COORD-AI': 'amber',
  'EVAC-AI': 'blue',
}

const connectionLabels: Record<WSConnectionState, string> = {
  connected: 'LIVE',
  reconnecting: 'RECONNECTING',
  offline: 'OFFLINE — SIMULATION',
  connecting: 'CONNECTING...',
}

function formatLiveTime() {
  return new Date().toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function formatMessageTime(timestamp: string) {
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) {
    return formatLiveTime()
  }

  return parsed.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function mapMessageToEntry(message: AgentMessage): AgentEntry {
  return {
    id: message.id,
    time: formatMessageTime(message.timestamp),
    agent: formatAgentName(message.sender),
    summary: extractMessageText(message),
    detail: message.reasoning.length > 0 ? message.reasoning.join(' → ') : 'No reasoning supplied by Group A.',
    severity: priorityToSeverity(message.priority),
    isNew: true,
  }
}

export function AgentFeed({ connectionState, incomingMessage, customEntry, onOverride }: AgentFeedProps) {
  const [feedEntries, setFeedEntries] = useState<AgentEntry[]>(initialEntries)
  const [openEntry, setOpenEntry] = useState<string | null>('initial-064211')
  const [showShap, setShowShap] = useState(true)
  const [overrideTarget, setOverrideTarget] = useState<{
    id: string
    agentType: string
    action: string
    reasoning: string
    shapFeatures?: import('../../../lib/mapTypes').ShapFeature[]
    confidence?: number
  } | null>(null)
  const [toast, setToast] = useState<{ message: string; visible: boolean }>({ message: '', visible: false })
  const feedListRef = useRef<HTMLDivElement | null>(null)
  const poolIndexRef = useRef(0)
  const liveIdRef = useRef(0)
  const animationTimersRef = useRef<number[]>([])
  const lastMessageIdRef = useRef<string | null>(null)

  const clearNewFlag = (id: string) => {
    const animationTimer = window.setTimeout(() => {
      setFeedEntries((currentEntries) =>
        currentEntries.map((entry) => (entry.id === id ? { ...entry, isNew: false } : entry)),
      )
    }, 400)

    animationTimersRef.current.push(animationTimer)
  }

  useEffect(() => {
    const addLiveEntry = () => {
      const poolEntry = liveEntryPool[poolIndexRef.current]
      const id = `live-${Date.now()}-${liveIdRef.current}`

      liveIdRef.current += 1
      poolIndexRef.current = (poolIndexRef.current + 1) % liveEntryPool.length

      setFeedEntries((currentEntries) => [
        {
          ...poolEntry,
          id,
          time: formatLiveTime(),
          isNew: true,
        },
        ...currentEntries,
      ].slice(0, 20))

      requestAnimationFrame(() => {
        feedListRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
      })

      clearNewFlag(id)
    }

    if (connectionState === 'connected') {
      return undefined
    }

    const firstEntryTimer = window.setTimeout(() => {
      addLiveEntry()
    }, 10000)

    const intervalTimer = window.setInterval(() => {
      addLiveEntry()
    }, 30000)

    return () => {
      window.clearTimeout(firstEntryTimer)
      window.clearInterval(intervalTimer)
      animationTimersRef.current.forEach(window.clearTimeout)
      animationTimersRef.current = []
    }
  }, [connectionState])

  useEffect(() => {
    if (connectionState !== 'connected' || !incomingMessage || incomingMessage.id === lastMessageIdRef.current) {
      return
    }

    lastMessageIdRef.current = incomingMessage.id
    const entry = mapMessageToEntry(incomingMessage)

    setFeedEntries((currentEntries) => [entry, ...currentEntries].slice(0, 20))

    requestAnimationFrame(() => {
      feedListRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
    })

    clearNewFlag(entry.id)
  }, [connectionState, incomingMessage])

  useEffect(() => {
    if (!customEntry) {
      return
    }

    const id = `custom-${Date.now()}-${Math.random()}`
    const entry: AgentEntry = {
      id,
      time: formatLiveTime(),
      agent: customEntry.agent,
      summary: customEntry.summary,
      detail: customEntry.detail,
      severity: customEntry.severity,
      isNew: true,
    }

    setFeedEntries((currentEntries) => [entry, ...currentEntries].slice(0, 20))
    setOpenEntry(id)

    requestAnimationFrame(() => {
      feedListRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
    })

    clearNewFlag(id)
  }, [customEntry])

  return (
    <section className="panel agent-panel" style={{ position: 'relative' }}>
      <div className="panel-title">
        <h2>AGENT ACTIVITY</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <button
            onClick={() => setShowShap(s => !s)}
            style={{
              padding: '2px 8px',
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.08em',
              background: showShap ? 'rgba(59,130,246,0.15)' : 'transparent',
              border: `1px solid ${showShap ? 'rgba(59,130,246,0.4)' : 'rgba(255,255,255,0.1)'}`,
              color: showShap ? '#60a5fa' : '#64748b',
              borderRadius: '3px',
              cursor: 'pointer',
            }}
          >
            {showShap ? '◈ EXPLAIN ON' : '◈ EXPLAIN OFF'}
          </button>
          <span
            className="live-badge"
            style={{
              color: connectionState === 'connected' ? '#00e676' : connectionState === 'reconnecting' ? '#ffaa00' : '#ff3b3b',
            }}
          >
            ● {connectionLabels[connectionState]}
          </span>
        </div>
      </div>
      <div className="feed-list" ref={feedListRef}>
        {feedEntries.map((entry) => {
          const isOpen = openEntry === entry.id
          const isHighSeverity = entry.severity === 'critical'
          return (
            <button
              className={`feed-entry ${isOpen ? 'is-open' : ''} ${entry.isNew ? 'feed-entry-new' : ''} ${isHighSeverity ? 'high-severity' : ''}`}
              type="button"
              key={entry.id}
              onClick={() => setOpenEntry(isOpen ? null : entry.id)}
            >
              <div className={`feed-main ${isHighSeverity ? 'has-critical' : ''}`}>
                <span className="timestamp">{entry.time}</span>
                {isHighSeverity && <span className="critical-label">⚠ CRITICAL</span>}
                <span className={`agent-badge ${isHighSeverity ? 'critical' : agentClass[entry.agent]}`}>{entry.agent}</span>
                <ChevronDown size={16} className="chevron" />
              </div>
              <p>{entry.summary}</p>
              {isOpen && (
                <>
                  <div className="reasoning">{entry.detail}</div>
                  {showShap && (
                    <ShapBadges
                      shap={entry.shap ?? SYNTHETIC_SHAP[entry.agent] ?? DEFAULT_SHAP}
                    />
                  )}
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '8px' }}>
                    {entry.overridden ? (
                      <span style={{
                        fontSize: '9px',
                        fontWeight: 700,
                        color: '#f59e0b',
                        letterSpacing: '0.05em',
                      }}>
                        ⚠ OVERRIDDEN  {entry.overrideRecord ? new Date(entry.overrideRecord.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false }) : ''}
                      </span>
                    ) : (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          const shap = entry.shap ?? SYNTHETIC_SHAP[entry.agent]
                          setOverrideTarget({
                            id: entry.id,
                            agentType: entry.agent,
                            action: entry.summary,
                            reasoning: entry.detail,
                            shapFeatures: shap?.topFeatures,
                            confidence: shap?.modelConfidence,
                          })
                        }}
                        style={{
                          padding: '2px 8px',
                          fontSize: '9px',
                          fontWeight: 700,
                          letterSpacing: '0.08em',
                          background: 'rgba(239,68,68,0.1)',
                          border: '1px solid rgba(239,68,68,0.3)',
                          color: '#ef4444',
                          borderRadius: '3px',
                          cursor: 'pointer',
                        }}
                      >
                        OVERRIDE
                      </button>
                    )}
                  </div>
                </>
              )}
            </button>
          )
        })}
      </div>

      {/* Override Panel */}
      <OverridePanel
        decision={overrideTarget}
        onClose={() => setOverrideTarget(null)}
        onConfirm={(reason) => {
          if (!overrideTarget) return
          // Mark as overridden locally
          setFeedEntries(prev =>
            prev.map(e =>
              e.id === overrideTarget.id
                ? {
                    ...e,
                    overridden: true,
                    overrideRecord: {
                      id: `override-${Date.now()}`,
                      agentDecisionId: overrideTarget.id,
                      agentType: overrideTarget.agentType,
                      originalAction: overrideTarget.action,
                      overrideReason: reason,
                      commanderId: 'CDR-SOHAM',
                      timestamp: Date.now(),
                      propagatedTo: [],
                    },
                  }
                : e
            )
          )
          // Fire onOverride to parent for the audit log
          overrideTarget && onOverride?.(overrideTarget.id, overrideTarget.agentType, overrideTarget.action, reason)
          // Show toast
          setToast({ message: 'Override logged. Propagated to dependent agents.', visible: true })
          setTimeout(() => setToast({ message: '', visible: false }), 4000)
        }}
      />

      {/* Toast */}
      {toast.visible && (
        <div style={{
          position: 'absolute',
          bottom: '8px',
          left: '8px',
          right: '8px',
          background: '#1e293b',
          border: '1px solid rgba(245,158,11,0.4)',
          color: '#f59e0b',
          fontSize: '10px',
          fontWeight: 600,
          padding: '8px 12px',
          borderRadius: '4px',
          textAlign: 'center',
          zIndex: 20,
          animation: 'fadeIn 0.2s ease',
        }}>
          {toast.message}
        </div>
      )}
    </section>
  )
}
