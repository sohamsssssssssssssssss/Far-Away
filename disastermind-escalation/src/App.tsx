import { useState } from 'react'
import { EscalationMemo } from './components/EscalationMemo'
import { ScenarioSelector } from './components/ScenarioSelector'
import type { Scenario } from './lib/scenarios'

function App() {
  const [selectedScenario, setSelectedScenario] = useState<Scenario | null>(null)

  return (
    <main className="app-shell">
      {selectedScenario ? (
        <EscalationMemo key={selectedScenario.id} scenario={selectedScenario} onReset={() => setSelectedScenario(null)} />
      ) : (
        <ScenarioSelector onSelect={setSelectedScenario} />
      )}
    </main>
  )
}

export default App
