import { Send } from 'lucide-react'

export function BriefingPanel() {
  return (
    <section className="panel briefing-panel">
      <div className="panel-title">
        <h2>LAST BRIEFING - 06:30:00</h2>
        <span>LLM SITREP</span>
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
