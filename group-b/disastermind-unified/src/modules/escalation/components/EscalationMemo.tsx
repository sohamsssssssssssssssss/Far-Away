import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { CountdownTimer } from './CountdownTimer'
import { DecisionPanel } from './DecisionPanel'
import { MemoCard } from './MemoCard'
import { generateEscalationMemo, type EscalationMemoFields } from '../lib/ollama'
import type { Scenario } from '../lib/scenarios'

type EscalationMemoProps = {
  scenario: Scenario
  onReset: () => void
}

export function EscalationMemo({ scenario, onReset }: EscalationMemoProps) {
  const [memo, setMemo] = useState<EscalationMemoFields | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    generateEscalationMemo(scenario.prompt)
      .then((nextMemo) => {
        if (!cancelled) {
          setMemo(nextMemo)
        }
      })
      .catch((nextError: unknown) => {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : 'Unable to generate escalation memo.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [scenario])

  return (
    <section className="memo-view">
      <header className="memo-header">
        <div className="pending-badge">
          <AlertTriangle size={18} />
          ESCALATION PENDING
        </div>
        <h1>{scenario.type}</h1>
        <p>AI-generated memo - awaiting commander decision</p>
        <CountdownTimer />
      </header>

      <MemoCard memo={memo} loading={loading} error={error} />
      <DecisionPanel disabled={loading || Boolean(error)} onReset={onReset} />
    </section>
  )
}
