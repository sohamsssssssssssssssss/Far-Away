import { useState, useCallback } from 'react'
import { Send, RefreshCw, Loader2 } from 'lucide-react'
import { generateSituationBriefing } from '../../../lib/situationBriefing'
import type { SituationBriefing } from '../../../lib/situationBriefing'

export function BriefingPanel({ isRiverWarning = false }: { isRiverWarning?: boolean }) {
  const [briefing, setBriefing] = useState<SituationBriefing | null>(null)
  const [loading, setLoading] = useState(false)

  const generateBriefing = useCallback(async () => {
    setLoading(true)
    try {
      const result = await generateSituationBriefing({
        eventName: 'Cyclone Remal',
        activeZones: ['Zone 7', 'Zone 4', 'Zone 2'],
        agentDecisionsSince: [
          'Rerouted 3 rescue boats to Zone 6',
          'Activated evacuation route ALPHA-3',
          'Elevated inundation risk in Zone 7 to HIGH',
          'Dispatched 2 medical units to Puri shelter',
          'Scaled back Zone 4 patrols by 1 unit',
        ],
        resourcesSummary: '15 boats, 5 helicopters, 10 medical units deployed',
        populationAtRisk: 84000,
        projectedNextHours: 'Inundation expected Zone 7 in 4.2 hours',
        pendingEscalations: 1,
      })
      setBriefing(result)
    } catch (err) {
      console.error('Failed to generate briefing:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const formattedTime = briefing
    ? new Date(briefing.generatedAt).toLocaleTimeString('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      })
    : null

  return (
    <section className="panel briefing-panel">
      <style>{`
        @keyframes flash-red-gauge {
          0%, 100% { background: rgba(10, 13, 20, 0.5); border-color: rgba(58, 74, 107, 0.68); }
          50% { background: rgba(255, 59, 59, 0.3); border-color: #ff3b3b; box-shadow: 0 0 10px rgba(255, 59, 59, 0.5); }
        }
        .flash-red {
          animation: flash-red-gauge 0.75s infinite;
        }
        .briefing-actions {
          display: flex;
          gap: 8px;
          margin-top: 12px;
        }
        .briefing-actions button {
          flex: 1;
        }
        .briefing-meta {
          margin-top: 8px;
          font-size: 10px;
          color: var(--text-secondary);
          font-family: var(--font-mono);
          text-align: center;
        }
        .briefing-loading {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          padding: 16px 0;
          color: var(--accent-primary);
          font-family: var(--font-mono);
          font-size: 11px;
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        .briefing-spinner {
          animation: spin 1s linear infinite;
        }
      `}</style>
      <div className="panel-title">
        <h2>{briefing ? 'SITUATION BRIEFING' : 'LAST BRIEFING - 06:30:00'}</h2>
        <span>LLM SITREP</span>
      </div>

      {/* Mahanadi River Gauge telemetry display */}
      <div className={`gauge-indicator-container ${isRiverWarning ? 'flash-red' : ''}`} style={{
        padding: '8px 12px',
        border: '1px solid rgba(58, 74, 107, .68)',
        background: 'rgba(10, 13, 20, 0.4)',
        borderRadius: '4px',
        marginBottom: '12px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        transition: 'all 0.3s ease'
      }}>
        <span style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-secondary)', fontFamily: 'var(--font-heading)' }}>
          MAHANADI RIVER GAUGE
        </span>
        <strong style={{
          fontSize: '13px',
          color: isRiverWarning ? '#ff3b3b' : 'var(--accent-primary)',
          fontFamily: 'var(--font-mono)'
        }}>
          {isRiverWarning ? '98.7% CRITICAL' : '91.2% WARNING'}
        </strong>
      </div>

      {loading ? (
        <div className="briefing-loading">
          <Loader2 size={16} className="briefing-spinner" />
          <span>Generating situation briefing…</span>
        </div>
      ) : briefing ? (
        <>
          <div className="briefing-copy">
            {briefing.text}
          </div>
          <div className="briefing-meta">
            Generated {formattedTime} via {briefing.provider}
          </div>
        </>
      ) : (
        <>
          <div className="briefing-copy">
            Since the last briefing, Cyclone Remal has intensified to Category 3. Inundation risk in Zones 6 and 7 has been elevated to HIGH by FLOOD-AI. The system has autonomously rerouted 3 rescue boats and activated evacuation route ALPHA-3. Currently 847 civilians have been moved to shelters; Puri shelter is at 73% capacity. In the next 2 hours, river gauge levels are projected to breach danger threshold in Zone 7. Two escalations require commander attention: mandatory evacuation order for Zone 7 (14,200 residents) and a cross-state boat request.
          </div>
          <div className="next-briefing">
            <span>NEXT BRIEFING IN</span>
            <strong>14:32</strong>
          </div>
        </>
      )}

      <div className="briefing-actions">
        {briefing ? (
          <button type="button" className="officials-btn" onClick={generateBriefing}>
            <RefreshCw size={16} />
            REFRESH
          </button>
        ) : (
          <button type="button" className="officials-btn" onClick={generateBriefing}>
            <Send size={16} />
            GENERATE BRIEFING
          </button>
        )}
        <button type="button" className="officials-btn" disabled={!briefing}>
          <Send size={16} />
          SEND TO OFFICIALS
        </button>
      </div>
    </section>
  )
}
