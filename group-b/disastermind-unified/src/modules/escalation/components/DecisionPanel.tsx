import { useMemo, useState } from 'react'
import { Check, RotateCcw, X } from 'lucide-react'

type DecisionPanelProps = {
  disabled: boolean
  onReset: () => void
}

type Decision = 'approved' | 'override-pending' | 'overridden' | null

const timestamp = () =>
  new Date().toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })

export function DecisionPanel({ disabled, onReset }: DecisionPanelProps) {
  const [decision, setDecision] = useState<Decision>(null)
  const [reason, setReason] = useState('')
  const [submittedReason, setSubmittedReason] = useState('')
  const [error, setError] = useState('')
  const [loggedAt, setLoggedAt] = useState('')

  const finalDecision = decision === 'approved' || decision === 'overridden'
  const buttonsDisabled = disabled || finalDecision || decision === 'override-pending'

  const commanderLine = useMemo(() => {
    if (!loggedAt) return ''
    return `${loggedAt} // Commander: COL. SHARMA`
  }, [loggedAt])

  const approve = () => {
    setDecision('approved')
    setLoggedAt(timestamp())
    setError('')
  }

  const submitOverride = () => {
    if (!reason.trim()) {
      setError('Override reason is required before logging commander override.')
      return
    }
    setSubmittedReason(reason.trim())
    setDecision('overridden')
    setLoggedAt(timestamp())
    setError('')
  }

  return (
    <section className="decision-panel">
      {!finalDecision && (
        <div className="decision-buttons">
          <button className="approve-button" type="button" disabled={buttonsDisabled} onClick={approve}>
            <Check size={22} />
            APPROVE
          </button>
          <button className="override-button" type="button" disabled={buttonsDisabled} onClick={() => setDecision('override-pending')}>
            <X size={22} />
            OVERRIDE
          </button>
        </div>
      )}

      {decision === 'override-pending' && (
        <div className="override-form">
          <label htmlFor="override-reason">Enter override reason (required):</label>
          <textarea
            id="override-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="State operational, legal, or intelligence grounds for override..."
          />
          {error && <p className="form-error">{error}</p>}
          <button type="button" onClick={submitOverride}>
            SUBMIT OVERRIDE
          </button>
        </div>
      )}

      {decision === 'approved' && (
        <div className="decision-log approved-log">
          <strong>DECISION LOGGED - APPROVED</strong>
          <p>Executing autonomous action...</p>
          <span>{commanderLine}</span>
        </div>
      )}

      {decision === 'overridden' && (
        <div className="decision-log overridden-log">
          <strong>DECISION LOGGED - OVERRIDDEN</strong>
          <p>{submittedReason}</p>
          <span>{commanderLine}</span>
        </div>
      )}

      {finalDecision && (
        <button className="new-escalation-button" type="button" onClick={onReset}>
          <RotateCcw size={16} />
          NEW ESCALATION
        </button>
      )}
    </section>
  )
}
