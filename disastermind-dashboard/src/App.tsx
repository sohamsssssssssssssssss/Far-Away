import './App.css'
import { AgentFeed } from './components/AgentFeed'
import { BriefingPanel } from './components/BriefingPanel'
import { EscalationQueue } from './components/EscalationQueue'
import { MockMap } from './components/MockMap'
import { ResourcePanel } from './components/ResourcePanel'
import { TopBar } from './components/TopBar'

function App() {
  return (
    <main className="command-shell">
      <TopBar />
      <section className="dashboard-grid" aria-label="DisasterMind commander dashboard">
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

export default App
