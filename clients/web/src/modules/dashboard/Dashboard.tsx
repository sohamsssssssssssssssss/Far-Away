import { useEffect, useState } from 'react'
import { useApiStatus } from '../../hooks/useApiStatus'
import { useMemo } from 'react'
import { useOverrides } from '../../hooks/useOverrides'
import { connectWebSocket } from '../../lib/disasterApi'
import type { AgentMessage, WSConnectionState } from '../../lib/disasterApi'
import { AgentFeed } from './components/AgentFeed'
import { BriefingPanel } from './components/BriefingPanel'
import { EscalationQueue } from './components/EscalationQueue'
import { LiveMap } from './components/LiveMap'
import { MapLegend } from './components/MapLegend'
import { ResourcePanel } from './components/ResourcePanel'
import { useDemoTimeline } from '../../lib/demoTimeline'
import { SYNTHETIC_MAP_STATE, IMD_ALERTS } from '../../lib/mapTypes'
import type { MapState, Shelter, EvacRouteShelter } from '../../lib/mapTypes'
import { useBackendWS } from '../../hooks/useBackendWS'
import type { BackendWSMessage } from '../../hooks/useBackendWS'
import { BackendStatusBadge } from '../../components/BackendStatusBadge'
import { PostIncidentReport } from '../../components/PostIncidentReport'
import type { AuditEntry } from '../../services/reportService'
import { fetchHealth } from '../../services/backendService'

function AlertTicker() {
  const alerts = IMD_ALERTS
  const [idx, setIdx] = useState(0)

  useEffect(() => {
    const t = setInterval(() => setIdx(i => (i + 1) % alerts.length), 5000)
    return () => clearInterval(t)
  }, [])

  const alert = alerts[idx]
  const color = alert.severity === 'RED' ? '#ef4444'
    : alert.severity === 'ORANGE' ? '#f97316' : '#eab308'

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      padding: '5px 16px',
      background: `linear-gradient(90deg, ${color}18 0%, transparent 100%)`,
      borderBottom: `1px solid ${color}30`,
      fontSize: '11px',
      fontFamily: 'monospace',
      overflow: 'hidden',
    }}>
      <span style={{
        color,
        fontWeight: 700,
        fontSize: '9px',
        letterSpacing: '0.1em',
        whiteSpace: 'nowrap',
        flexShrink: 0,
      }}>
        ⚠ IMD {alert.severity}
      </span>
      <span style={{ color: '#cbd5e1', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {alert.headline}
      </span>
      <span style={{ color: '#475569', fontSize: '9px', whiteSpace: 'nowrap', flexShrink: 0 }}>
        {alert.district}
      </span>
    </div>
  )
}

function formatStatusTime(timestamp: string) {
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) {
    return '--:--:--'
  }

  return parsed.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function Dashboard() {
  const { status, setWsState, recordMessage } = useApiStatus()
  const [latestMessage, setLatestMessage] = useState<AgentMessage | null>(null)
  const [lastMessageTime, setLastMessageTime] = useState('--:--:--')
  const connectionState: WSConnectionState = status.backendOnline ? status.wsState : 'offline'

  // Demo Timeline States
  const [customFeedEntry, setCustomFeedEntry] = useState<{
    agent: string
    summary: string
    detail: string
    severity?: 'critical' | 'high' | 'medium' | 'low' | 'info'
  } | null>(null)
  const [timelineEscalations, setTimelineEscalations] = useState<any[]>([])
  const [zone7OverrideState, setZone7OverrideState] = useState<'pending' | 'auto-executing' | 'approved' | 'overridden' | 'removed'>('pending')
  const [showAutoExecBanner, setShowAutoExecBanner] = useState(false)
  const [boatsAdjustment, setBoatsAdjustment] = useState(0)
  const [mapState, setMapState] = useState<MapState>(SYNTHETIC_MAP_STATE)

  const { overrides, submitOverride } = useOverrides()
  const [overrideLogOpen, setOverrideLogOpen] = useState(false)
  const [liveShelters, setLiveShelters] = useState<Shelter[] | undefined>(undefined)
  const [backendWSLastMsgAt, setBackendWSLastMsgAt] = useState<number>(Date.now())

  const [showReport, setShowReport] = useState(false)

  // Assemble audit log from available data sources
  const auditLog: AuditEntry[] = useMemo(() => {
    const entries: AuditEntry[] = []

    // Override records
    overrides.forEach(r => {
      entries.push({
        id: r.id,
        timestamp: r.timestamp,
        agentId: r.agentType,
        action: 'OVERRIDE',
        operatorId: r.commanderId,
        note: r.overrideReason,
        payload: { originalAction: r.originalAction, propagatedTo: r.propagatedTo },
      })
    })

    // Escalation records
    // (escalations are managed in useEscalations inside EscalationQueue,
    //  not directly accessible here. We add what we can from the demo state.)
    if (timelineEscalations.length > 0) {
      timelineEscalations.forEach(esc => {
        if (zone7OverrideState === 'approved') {
          entries.push({
            id: `esc-approve-${Date.now()}`,
            timestamp: Date.now(),
            agentId: 'COMMANDER-AI',
            action: 'APPROVE',
            operatorId: 'CDR-SOHAM',
            note: esc.situation,
            payload: esc,
          })
        }
        if (zone7OverrideState === 'auto-executing') {
          entries.push({
            id: `esc-auto-${Date.now()}`,
            timestamp: Date.now(),
            agentId: 'COMMANDER-AI',
            action: 'AUTO_EXECUTED',
            note: `Auto-executed: ${esc.situation}`,
            payload: esc,
          })
        }
      })
    }

    entries.sort((a, b) => b.timestamp - a.timestamp)
    return entries
  }, [overrides, timelineEscalations, zone7OverrideState])

  // Health check on mount
  useEffect(() => {
    fetchHealth().then(ok => {
      if (ok) {
        console.log('Group A backend: LIVE — far-away-production.up.railway.app')
      } else {
        console.log('Group A backend: OFFLINE — using mock data')
      }
    })
  }, [])

  // Backend WebSocket for live message stream
  const { connectionState: backendWSState } = useBackendWS((msg: BackendWSMessage) => {
    // Update last message timestamp for staleness detection
    setBackendWSLastMsgAt(Date.now())

    // Log all non-routine frames
    if (msg.type !== 'acknowledgement') {
      console.debug('[BackendWS]', msg.topic, msg.type, msg.sender)
    }

    // instruction messages → prepend to agent feed
    if (msg.type === 'instruction') {
      const agentMsg: AgentMessage = {
        id: msg.id,
        sender: msg.sender,
        recipient: msg.recipient,
        type: 'instruction',
        priority: msg.priority as 1 | 2 | 3 | 4 | 5,
        payload: msg.payload,
        reasoning: msg.reasoning,
        ttl_seconds: msg.ttl_seconds,
        topic: msg.topic,
        incident_id: msg.incident_id,
        module: msg.module as 'A' | 'B' | 'C' | 'ALL',
        escalation_trigger: msg.escalation_trigger,
        timestamp: msg.timestamp,
      }
      setLatestMessage(agentMsg)
      setLastMessageTime(formatStatusTime(msg.timestamp))
      recordMessage()
    }

    // escalation messages — handled by EscalationQueue via incomingMessage
    if (msg.type === 'escalation') {
      const escMsg: AgentMessage = {
        id: msg.id,
        sender: msg.sender,
        recipient: msg.recipient,
        type: 'escalation',
        priority: msg.priority as 1 | 2 | 3 | 4 | 5,
        payload: msg.payload,
        reasoning: msg.reasoning,
        ttl_seconds: msg.ttl_seconds,
        topic: msg.topic,
        incident_id: msg.incident_id,
        module: msg.module as 'A' | 'B' | 'C' | 'ALL',
        escalation_trigger: msg.escalation_trigger,
        timestamp: msg.timestamp,
      }
      setLatestMessage(escMsg)
    }

    // tier2.routing_plan → update live shelter markers
    if (msg.topic === 'tier2.routing_plan') {
      const payloadShelters = msg.payload?.shelters as EvacRouteShelter[] | undefined
      if (payloadShelters && Array.isArray(payloadShelters)) {
        setLiveShelters(prev => {
          const merged = prev ? [...prev] : []
          payloadShelters.forEach(es => {
            const idx = merged.findIndex(s => s.id === es.shelter_id)
            const shelter: Shelter = {
              id: es.shelter_id,
              name: es.name,
              district: '', // not provided by EvacRoute
              lat: es.location.lat,
              lon: es.location.lon,
              capacity: es.capacity,
              occupied: es.current_occupancy ?? 0,
              status: es.status === 'full' ? 'FULL' : es.status === 'closed' ? 'CLOSED' : 'OPEN',
              facilities: [],
            }
            if (idx >= 0) {
              merged[idx] = shelter
            } else {
              merged.push(shelter)
            }
          })
          return merged
        })
      }
    }
  })

  // Demo Timeline callbacks
  const {
    demoStatus,
    startDemo,
    escalationApproved,
    setEscalationApproved,
    elapsedSeconds
  } = useDemoTimeline({
    onRiverWarning: () => {
      setCustomFeedEntry({
        agent: 'FLOOD-AI',
        summary: 'Mahanadi river gauge CRITICAL — 98.7% capacity. Breach probability 84% within 6 hours.',
        detail: 'Historical breach threshold exceeded → cascade flood model triggered → Zone 7 inundation projected',
        severity: 'critical'
      })
    },
    onZone7Escalation: () => {
      setZone7OverrideState('pending')
      setTimelineEscalations([
        {
          id: 'evac-zone-7-escalation',
          title: 'MANDATORY EVACUATION — ZONE 7',
          situation: '84,000 residents. FLOOD-AI projects inundation in 4.2 hours. Evacuation window: 2.8 hours.',
          recommended: 'APPROVE',
          decisionRequiredBy: new Date(Date.now() + 120000).toISOString(),
          source: 'mock'
        }
      ])
      setCustomFeedEntry({
        agent: 'COMMANDER-AI',
        summary: 'Escalating Zone 7 mandatory evacuation to human commander. Authority threshold exceeded.',
        detail: 'Authority threshold exceeded.',
        severity: 'high'
      })
      window.dispatchEvent(new CustomEvent('flash-escalation-tab'))
    },
    onAutoExecute: () => {
      setZone7OverrideState('auto-executing')
      setShowAutoExecBanner(true)
      setTimeout(() => setShowAutoExecBanner(false), 8000)

      setCustomFeedEntry({
        agent: 'COMMANDER-AI',
        summary: 'Auto-executed Zone 7 evacuation. Human window expired. 847 field units notified.',
        detail: 'Commander override window missed → automatic system execution triggered → notifications broadcasted',
        severity: 'critical'
      })

      setTimeout(() => {
        setZone7OverrideState('removed')
        setTimelineEscalations([])
      }, 3000)
    },
    onAllClear: () => {
      setCustomFeedEntry({
        agent: 'PREDICTION-AI',
        summary: 'Cyclone Remal weakening. Wind speed reduced to 94 km/h. Zone 7 evacuation proceeding.',
        detail: 'Cyclone Remal weakening.',
        severity: 'medium'
      })
      setBoatsAdjustment(-2)
      window.dispatchEvent(new CustomEvent('cyclone-badge-amber'))
    }
  })

  // Watch for elapsedSeconds === 360 and escalationApproved for the human approved feed entry
  useEffect(() => {
    if (elapsedSeconds === 360 && escalationApproved) {
      setCustomFeedEntry({
        agent: 'COMMANDER-AI',
        summary: 'Human commander approved Zone 7 evacuation. Executing with human authority.',
        detail: 'Human commander approved Zone 7 evacuation.',
        severity: 'high'
      })
    }
  }, [elapsedSeconds, escalationApproved])

  const handleStartDemo = () => {
    // Reset all states
    setCustomFeedEntry(null)
    setTimelineEscalations([])
    setZone7OverrideState('pending')
    setShowAutoExecBanner(false)
    setBoatsAdjustment(0)
    window.dispatchEvent(new CustomEvent('cyclone-badge-red'))
    startDemo()
  }

  const handleApproveZone7 = () => {
    setZone7OverrideState('approved')
    setEscalationApproved(true)
  }

  const briefingContext = useMemo(() => ({
    activeZones: ['Zone 6', 'Zone 7'],
    agentDecisionCount: 12,
    deployedBoats: 12,
    deployedHelicopters: 4,
    shelterUtilisation: 73,
    topRiskZone: 'Zone 7',
    topRiskProbability: 0.92,
    lastEscalation: 'Mandatory evacuation — Zone 7',
    minutesSinceLastBriefing: 15,
  }), [])

  // GPS drift simulation — every 8 seconds, jiggle team positions slightly
  useEffect(() => {
    const interval = setInterval(() => {
      setMapState(prev => {
        const teams = { ...prev.teams }
        Object.keys(teams).forEach(id => {
          // Small lat/lon deltas comparable to the pixel drift in MockMap
          const maxDelta = id === 'UNIT-C1' ? 0.006 : 0.003
          const dLat = (Math.random() * 2 - 1) * maxDelta
          const dLon = (Math.random() * 2 - 1) * maxDelta
          teams[id] = {
            ...teams[id],
            location: {
              lat: teams[id].location.lat + dLat,
              lon: teams[id].location.lon + dLon,
            },
          }
        })
        return { ...prev, teams }
      })
    }, 8000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    const disconnect = connectWebSocket(
      (message) => {
        setLatestMessage(message)
        setLastMessageTime(formatStatusTime(message.timestamp))
        recordMessage()

        // Group A WebSocket integration — update map state from live telemetry
        if (message.topic === 'tier3.iot_telemetry') {
          const p = message.payload as Record<string, unknown> | undefined
          if (p?.kind === 'gps_beacon') {
            const readings = (p.readings ?? []) as Array<{
              team_id: string
              location: { lat: number; lon: number }
              status?: 'active' | 'staged' | 'distress' | 'offline'
              timestamp: string
            }>
            readings.forEach(r => {
              setMapState(prev => ({
                ...prev,
                teams: {
                  ...prev.teams,
                  [r.team_id]: {
                    team_id: r.team_id,
                    location: r.location,
                    status: r.status ?? 'active',
                    timestamp: r.timestamp,
                  },
                },
              }))
            })
          }
        }

        if (message.topic === 'tier2.prediction') {
          const p = message.payload as Record<string, unknown> | undefined
          if (p?.risk_cells) {
            setMapState(prev => ({
              ...prev,
              riskCells: p.risk_cells as typeof prev.riskCells,
            }))
          }
        }
      },
      (state) => {
        setWsState(state)
      },
    )

    return () => {
      disconnect()
    }
  }, [setWsState, recordMessage])

  return (
    <main className="dashboard-module" aria-label="DisasterMind commander dashboard">
      <style>{`
        .auto-exec-banner {
          position: fixed;
          top: -100px;
          left: 50%;
          transform: translateX(-50%);
          width: 90%;
          max-width: 800px;
          background: #00e676;
          color: #05080c;
          padding: 14px 24px;
          border-radius: 0 0 8px 8px;
          box-shadow: 0 4px 20px rgba(0, 230, 118, 0.4);
          z-index: 1000;
          transition: top 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
          font-family: var(--font-heading);
          text-align: center;
        }
        .auto-exec-banner.visible {
          top: 0;
        }
        .banner-content {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 2px;
        }
        .banner-content strong {
          font-size: 13px;
          letter-spacing: 0.5px;
        }
        .banner-content span {
          font-size: 11px;
          opacity: 0.9;
          font-weight: 600;
        }
        @keyframes pulse-green-dot-anim {
          0% { opacity: 0.3; transform: scale(0.8); }
          100% { opacity: 1; transform: scale(1.2); }
        }
        .pulsing-green-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background-color: #00e676;
          box-shadow: 0 0 6px #00e676;
          animation: pulse-green-dot-anim 0.8s infinite alternate;
          display: inline-block;
          margin-right: 6px;
        }
        .demo-btn {
          margin-left: 12px;
          background: rgba(0, 230, 118, 0.1);
          border: 1px solid rgba(0, 230, 118, 0.4);
          color: #00e676;
          padding: 2px 8px;
          border-radius: 3px;
          cursor: pointer;
          font: 700 10px var(--font-mono);
          display: flex;
          align-items: center;
          gap: 4px;
          transition: all 0.2s;
        }
        .demo-btn:hover {
          background: rgba(0, 230, 118, 0.2);
          border-color: #00e676;
        }
      `}</style>

      {/* Auto-Execution banner */}
      <div className={`auto-exec-banner ${showAutoExecBanner ? 'visible' : ''}`}>
        <div className="banner-content">
          <strong>EVACUATION ORDER ISSUED — COMMANDER-AI AUTO-EXECUTED AFTER 120s WINDOW</strong>
          <span>84,000 RESIDENTS NOTIFIED VIA SMS/BROADCAST</span>
        </div>
      </div>

      <div className="dashboard-status-bar" aria-label="Group A backend status">
        <span className={status.backendOnline ? 'status-online' : 'status-offline'}>
          ● {status.backendOnline ? 'GROUP A CONNECTED' : 'GROUP A OFFLINE'}
        </span>
        <span className="status-separator">|</span>
        <span>{connectionState === 'connected' ? 'WS LIVE' : connectionState === 'connecting' ? 'WS CONNECTING...' : 'WS RECONNECTING...'}</span>
        <span className="status-separator">|</span>
        <span>LAST MSG {lastMessageTime}</span>
        <span className="status-separator">|</span>
        <BackendStatusBadge
          connectionState={backendWSState}
          lastMessageTime={backendWSLastMsgAt}
        />
        <button
          type="button"
          onClick={() => setShowReport(true)}
          style={{
            marginLeft: '12px',
            padding: '4px 14px',
            fontSize: '11px',
            fontWeight: 600,
            background: '#6366f1',
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            letterSpacing: '0.03em',
          }}
        >
          Generate Report
        </button>
        {!status.backendOnline && (
          <>
            <span className="status-separator">|</span>
            <span className="status-fallback">Fallback: SIMULATION MODE ACTIVE</span>
            <button
              type="button"
              onClick={handleStartDemo}
              className="demo-btn"
            >
              {demoStatus === 'idle' && '▶ START DEMO'}
              {demoStatus === 'running' && (
                <>
                  <span className="pulsing-green-dot" />
                  DEMO RUNNING
                </>
              )}
              {demoStatus === 'completed' && '↺ RESTART DEMO'}
            </button>
          </>
        )}
      </div>
      <AlertTicker />
      <section className="dashboard-grid">
        <aside className="side-column left-column">
          <ResourcePanel boatsAdjustment={boatsAdjustment} />
          <AgentFeed
            connectionState={connectionState}
            incomingMessage={latestMessage}
            customEntry={customFeedEntry}
            onOverride={submitOverride}
          />
        </aside>
        <section className="center-column" aria-label="Operational map">
          <div style={{ position: 'relative', height: '100%' }}>
            <LiveMap mapState={mapState} liveShelters={liveShelters} />
            <MapLegend />
          </div>
        </section>
        <aside className="side-column right-column">
          <EscalationQueue
            backendOnline={status.backendOnline}
            incomingMessage={latestMessage}
            timelineEscalations={timelineEscalations}
            onApproveZone7={handleApproveZone7}
            zone7OverrideState={zone7OverrideState}
          />
          {/* Override Log */}
          <section className="panel" style={{ display: 'flex', flexDirection: 'column' }}>
            <button
              onClick={() => setOverrideLogOpen(o => !o)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                width: '100%',
                padding: '8px 12px',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: '#94a3b8',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.08em', color: '#475569' }}>
                  OVERRIDE LOG
                </span>
                <span style={{
                  fontSize: '9px',
                  fontWeight: 700,
                  color: overrides.length > 0 ? '#f59e0b' : '#475569',
                }}>
                  [{overrides.length} {overrides.length === 1 ? 'entry' : 'entries'}]
                </span>
              </div>
              <span style={{ fontSize: '12px', transform: overrideLogOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>
                ▾
              </span>
            </button>
            {overrideLogOpen && (
              <div style={{ padding: '0 12px 8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {overrides.length === 0 ? (
                  <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic', padding: '4px 0' }}>
                    No overrides logged this session
                  </div>
                ) : (
                  overrides.map(rec => (
                    <div
                      key={rec.id}
                      style={{
                        fontSize: '10px',
                        color: '#cbd5e1',
                        borderTop: '1px solid rgba(255,255,255,0.04)',
                        padding: '6px 0',
                        lineHeight: 1.6,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' }}>
                        <span style={{ color: '#64748b', fontSize: '9px' }}>
                          {rec.id}  {rec.agentType}
                        </span>
                        <span style={{ color: '#475569', fontSize: '9px' }}>{rec.commanderId}</span>
                        <span style={{ color: '#475569', fontSize: '9px', marginLeft: 'auto' }}>
                          {new Date(rec.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
                        </span>
                      </div>
                      <div style={{ color: '#f59e0b', fontSize: '10px', fontStyle: 'italic', marginBottom: '2px' }}>
                        "{rec.overrideReason}"
                      </div>
                      <div style={{ color: '#475569', fontSize: '9px' }}>
                        Propagated to: {rec.propagatedTo.join(' · ')}
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </section>

          <BriefingPanel
            context={briefingContext}
          />
        </aside>
      </section>

      {/* Post-Incident Report Modal */}
      {showReport && (
        <PostIncidentReport
          auditLog={auditLog}
          onClose={() => setShowReport(false)}
        />
      )}
    </main>
  )
}
