import { LoaderCircle } from 'lucide-react'
import type { EscalationMemoFields } from '../lib/ollama'

type MemoCardProps = {
  memo: EscalationMemoFields | null
  loading: boolean
  error: string | null
}

const rows: Array<{ label: string; key: keyof EscalationMemoFields }> = [
  { label: 'SITUATION', key: 'situation' },
  { label: 'RECOMMENDED', key: 'recommended' },
  { label: 'RISK IF YES', key: 'riskIfYes' },
  { label: 'RISK IF NO', key: 'riskIfNo' },
]

export function MemoCard({ memo, loading, error }: MemoCardProps) {
  if (loading) {
    return (
      <section className="memo-card loading-card" aria-busy="true">
        <div className="loading-line">
          <LoaderCircle className="spinner" size={20} />
          DISASTERMIND AI GENERATING MEMO...
        </div>
        {rows.map((row) => (
          <div className="memo-row skeleton-row" key={row.label}>
            <span>{row.label}</span>
            <div className="skeleton-copy">
              <i />
              <i />
            </div>
          </div>
        ))}
      </section>
    )
  }

  if (error) {
    return (
      <section className="memo-card error-card" role="alert">
        <strong>CLAUDE API LINK FAILED</strong>
        <p>{error}</p>
      </section>
    )
  }

  if (!memo) {
    return null
  }

  return (
    <section className="memo-card">
      {rows.map((row) => (
        <div className="memo-row" key={row.label}>
          <span>{row.label}</span>
          <p>{memo[row.key]}</p>
        </div>
      ))}
    </section>
  )
}
