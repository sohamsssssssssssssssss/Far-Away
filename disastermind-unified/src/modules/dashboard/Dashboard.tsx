import { AgentFeed } from './components/AgentFeed'
import { BriefingPanel } from './components/BriefingPanel'
import { EscalationQueue } from './components/EscalationQueue'
import { MockMap } from './components/MockMap'
import { ResourcePanel } from './components/ResourcePanel'

export function Dashboard() {
  return (
    <main className="dashboard-module" aria-label="DisasterMind commander dashboard">
      <section className="dashboard-grid">
        <aside className="side-column left-column">
          <ResourcePanel />
          <AgentFeed />
        </aside>
        <section className="center-column" aria-label="Operational map">
          <MockMap />
        </section>
        <aside className="side-column right-column">
          <EscalationQueue />
          <BriefingPanel />
        </aside>
      </section>
    </main>
  )
}
