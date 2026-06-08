import { useState } from 'react'
import { EscalationMemo } from './components/EscalationMemo'
import { ScenarioSelector } from './components/ScenarioSelector'
import type { Scenario } from './lib/scenarios'

export function Escalation() {
  const [selectedScenario, setSelectedScenario] = useState<Scenario | null>(null)

  return (
    <main className="escalation-module">
      {selectedScenario ? (
        <EscalationMemo key={selectedScenario.id} scenario={selectedScenario} onReset={() => setSelectedScenario(null)} />
      ) : (
        <ScenarioSelector onSelect={setSelectedScenario} />
      )}
    </main>
  )
}
