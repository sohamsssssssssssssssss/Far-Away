import { useState } from 'react'
import { ChevronDown } from 'lucide-react'

const entries = [
  {
    time: '06:42:11',
    agent: 'FLOOD-AI',
    summary: 'Inundation risk elevated to HIGH in Zone 7',
    detail: 'Mahanadi gauge telemetry rose faster than the 20-minute model band. Tide surge plus upstream release raises breach probability near Satapada.',
  },
  {
    time: '06:41:58',
    agent: 'RESOURCE-AI',
    summary: 'Rerouted 3 boats from Zone 4 to Zone 7',
    detail: 'Zone 4 demand dropped after shelter intake stabilized. Zone 7 projected deficit crosses the NDRF dispatch threshold within 38 minutes.',
  },
  {
    time: '06:41:33',
    agent: 'EVAC-AI',
    summary: 'Evacuation route ALPHA-3 marked congested',
    detail: 'Traffic speed fell below 12 km/h near Pipili junction. BETA-1 remains viable for light vehicles and buses from Zone 5.',
  },
  {
    time: '06:40:55',
    agent: 'COORD-AI',
    summary: 'TEAM-04 redirected to Balasore shelter',
    detail: 'Balasore School shelter requested triage support. TEAM-04 is closest with medical supplies and a clear route window.',
  },
  {
    time: '06:40:12',
    agent: 'FLOOD-AI',
    summary: 'River gauge Mahanadi: 91% danger level',
    detail: 'Gauge trend is accelerating with a 14-minute doubling in rise rate. Confidence is high after cross-checking CWC telemetry.',
  },
  {
    time: '06:39:44',
    agent: 'RESOURCE-AI',
    summary: 'Medical unit staged at Puri district HQ',
    detail: 'Puri shelter occupancy reached 73%. District HQ offers generator backup and ambulance access to both ALPHA and GAMMA corridors.',
  },
  {
    time: '06:39:01',
    agent: 'EVAC-AI',
    summary: 'Route BETA-1 activated for Zone 5',
    detail: 'BETA-1 has lower treefall exposure and passes two fuel points. Police barricade coordination sent to Khordha control.',
  },
  {
    time: '06:38:30',
    agent: 'COORD-AI',
    summary: 'Mutual aid request sent to Andhra Pradesh',
    detail: 'Boat inventory model forecasts an 8-unit shortfall. Request package includes staging at Ichchapuram and handoff to NDRF sector command.',
  },
]

const agentClass: Record<string, string> = {
  'FLOOD-AI': 'cyan',
  'RESOURCE-AI': 'green',
  'COORD-AI': 'amber',
  'EVAC-AI': 'blue',
}

export function AgentFeed() {
  const [openEntry, setOpenEntry] = useState<string | null>('06:42:11')

  return (
    <section className="panel agent-panel">
      <div className="panel-title">
        <h2>AGENT ACTIVITY</h2>
        <span className="live-badge">● LIVE</span>
      </div>
      <div className="feed-list">
        {entries.map((entry) => {
          const isOpen = openEntry === entry.time
          return (
            <button
              className={`feed-entry ${isOpen ? 'is-open' : ''}`}
              type="button"
              key={entry.time}
              onClick={() => setOpenEntry(isOpen ? null : entry.time)}
            >
              <div className="feed-main">
                <span className="timestamp">{entry.time}</span>
                <span className={`agent-badge ${agentClass[entry.agent]}`}>{entry.agent}</span>
                <ChevronDown size={16} className="chevron" />
              </div>
              <p>{entry.summary}</p>
              {isOpen && <div className="reasoning">{entry.detail}</div>}
            </button>
          )
        })}
      </div>
    </section>
  )
}
