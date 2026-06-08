import { Send } from 'lucide-react'

export function BriefingPanel({ isRiverWarning = false }: { isRiverWarning?: boolean }) {
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
      `}</style>
      <div className="panel-title">
        <h2>LAST BRIEFING - 06:30:00</h2>
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

      <div className="briefing-copy">
        Since the last briefing, Cyclone Remal has intensified to Category 3. Inundation risk in Zones 6 and 7 has been elevated to HIGH by FLOOD-AI. The system has autonomously rerouted 3 rescue boats and activated evacuation route ALPHA-3. Currently 847 civilians have been moved to shelters; Puri shelter is at 73% capacity. In the next 2 hours, river gauge levels are projected to breach danger threshold in Zone 7. Two escalations require commander attention: mandatory evacuation order for Zone 7 (14,200 residents) and a cross-state boat request.
      </div>
      <div className="next-briefing">
        <span>NEXT BRIEFING IN</span>
        <strong>14:32</strong>
      </div>
      <button type="button" className="officials-btn">
        <Send size={16} />
        SEND TO OFFICIALS
      </button>
    </section>
  )
}
