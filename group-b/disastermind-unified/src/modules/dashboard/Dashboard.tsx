import { useEffect, useState } from 'react'
import { useApiStatus } from '../../hooks/useApiStatus'
import { connectWebSocket } from '../../lib/disasterApi'
import type { AgentMessage, WSConnectionState } from '../../lib/disasterApi'
import { AgentFeed } from './components/AgentFeed'
import { BriefingPanel } from './components/BriefingPanel'
import { EscalationQueue } from './components/EscalationQueue'
import { MockMap } from './components/MockMap'
import { ResourcePanel } from './components/ResourcePanel'
import { useDemoTimeline } from '../../lib/demoTimeline'

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
  const [isRiverWarningActive, setIsRiverWarningActive] = useState(false)
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

  // Demo Timeline callbacks
  const {
    demoStatus,
    startDemo,
    escalationApproved,
    setEscalationApproved,
    elapsedSeconds
  } = useDemoTimeline({
    onRiverWarning: () => {
      setIsRiverWarningActive(true)
      setTimeout(() => setIsRiverWarningActive(false), 3000)

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
    setIsRiverWarningActive(false)
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

  useEffect(() => {
    const disconnect = connectWebSocket(
      (message) => {
        setLatestMessage(message)
        setLastMessageTime(formatStatusTime(message.timestamp))
        recordMessage()
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
      <section className="dashboard-grid">
        <aside className="side-column left-column">
          <ResourcePanel boatsAdjustment={boatsAdjustment} />
          <AgentFeed
            connectionState={connectionState}
            incomingMessage={latestMessage}
            customEntry={customFeedEntry}
          />
        </aside>
        <section className="center-column" aria-label="Operational map">
          <MockMap isRiverWarning={isRiverWarningActive} />
        </section>
        <aside className="side-column right-column">
          <EscalationQueue
            backendOnline={status.backendOnline}
            incomingMessage={latestMessage}
            timelineEscalations={timelineEscalations}
            onApproveZone7={handleApproveZone7}
            zone7OverrideState={zone7OverrideState}
          />
          <BriefingPanel isRiverWarning={isRiverWarningActive} />
        </aside>
      </section>
    </main>
  )
}
