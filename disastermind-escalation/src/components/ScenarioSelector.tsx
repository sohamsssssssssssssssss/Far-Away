import { AlertTriangle, ArrowRight, RadioTower } from 'lucide-react'
import { scenarios, type Scenario } from '../lib/scenarios'

type ScenarioSelectorProps = {
  onSelect: (scenario: Scenario) => void
}

export function ScenarioSelector({ onSelect }: ScenarioSelectorProps) {
  return (
    <section className="selector-view">
      <div className="system-kicker">
        <RadioTower size={18} />
        CLAUDE-LINKED DECISION GATEWAY
      </div>
      <header className="selector-header">
        <h1>DISASTERMIND // ESCALATION SYSTEM</h1>
        <p>Select a scenario to generate an escalation memo</p>
      </header>

      <div className="scenario-stack" aria-label="Escalation scenarios">
        {scenarios.map((scenario, index) => (
          <button className="scenario-button" type="button" key={scenario.id} onClick={() => onSelect(scenario)}>
            <span className="scenario-index">{String(index + 1).padStart(2, '0')}</span>
            <span className="scenario-copy">
              <strong>{scenario.label}</strong>
              <span>{scenario.context}</span>
            </span>
            <span className="scenario-action">
              <AlertTriangle size={17} />
              GENERATE <ArrowRight size={18} />
            </span>
          </button>
        ))}
      </div>
    </section>
  )
}
