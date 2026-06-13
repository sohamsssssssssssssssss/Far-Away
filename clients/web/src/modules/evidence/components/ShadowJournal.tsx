import { useEffect, useState } from 'react'
import { ShieldCheck, ShieldX } from 'lucide-react'
import type { ShadowJournalDoc, ShadowRecord } from '../types'

// Canonical JSON matching Python's json.dumps(payload, sort_keys=True,
// separators=(",", ":")) — so the chain hash can be re-verified in the browser.
function canon(v: unknown): string {
  if (v === null) return 'null'
  if (typeof v === 'string') return JSON.stringify(v)
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return `[${v.map(canon).join(',')}]`
  const o = v as Record<string, unknown>
  return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canon(o[k])}`).join(',')}}`
}

async function sha256hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

interface Row {
  id: string
  hazard: string
  issued_at: string
  probability: number
  threshold: number
  occurred: boolean | null
}

function verdict(r: Row): { label: string; cls: string } {
  if (r.occurred === null) return { label: 'unresolved', cls: 'v-pending' }
  const alert = r.probability >= r.threshold
  if (alert && r.occurred) return { label: 'hit', cls: 'v-hit' }
  if (alert && !r.occurred) return { label: 'false alarm', cls: 'v-false' }
  if (!alert && r.occurred) return { label: 'miss', cls: 'v-miss' }
  return { label: 'correct (no alert)', cls: 'v-correct' }
}

export function ShadowJournal() {
  const [doc, setDoc] = useState<ShadowJournalDoc | null>(null)
  const [chain, setChain] = useState<'checking' | 'intact' | 'broken'>('checking')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const base = import.meta.env.BASE_URL || '/'
    fetch(`${base}data/shadow_journal_sample.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(async (d: ShadowJournalDoc) => {
        setDoc(d)
        let prev = d.genesis
        let ok = true
        for (const rec of d.records) {
          const h = await sha256hex(`${prev}|${canon(rec.payload)}`)
          if (h !== rec.hash) { ok = false; break }
          prev = rec.hash
        }
        setChain(ok ? 'intact' : 'broken')
      })
      .catch((e) => { setError(String(e)); setChain('broken') })
  }, [])

  const rows: Row[] = []
  if (doc) {
    const preds = doc.records.filter((r: ShadowRecord) => r.kind === 'prediction')
    const outcomes = new Map<string, boolean | null>()
    doc.records
      .filter((r) => r.kind === 'outcome')
      .forEach((r) => outcomes.set(String(r.payload.prediction_id), (r.payload.occurred as boolean) ?? null))
    preds.forEach((r) => {
      const p = r.payload
      const id = String(p.prediction_id)
      rows.push({
        id,
        hazard: String(p.hazard ?? '—'),
        issued_at: String(p.issued_at ?? '—'),
        probability: Number(p.probability ?? 0),
        threshold: Number(p.threshold ?? 0.5),
        occurred: outcomes.has(id) ? (outcomes.get(id) ?? null) : null,
      })
    })
  }

  return (
    <div className="evidence-pane">
      <div className="evidence-head">
        <h2>Shadow-Mode Journal</h2>
        <p className="evidence-sub">
          Append-only, hash-chained log of predictions recorded <em>before</em> the
          outcome is known — the tamper-evident record a live shadow season produces.
          The chain is re-verified in your browser.
        </p>
      </div>

      <div className="shadow-bar">
        {chain === 'checking' && <span className="chain-badge chain-checking">verifying chain…</span>}
        {chain === 'intact' && (
          <span className="chain-badge chain-ok"><ShieldCheck size={16} /> chain intact · {doc?.records.length ?? 0} records re-hashed in-browser</span>
        )}
        {chain === 'broken' && (
          <span className="chain-badge chain-bad"><ShieldX size={16} /> chain verification failed</span>
        )}
      </div>

      {error && <div className="evidence-error">Could not load journal: {error}</div>}
      {doc?._note && <p className="sample-banner">{doc._note}</p>}

      <table className="shadow-table">
        <thead>
          <tr><th>Prediction</th><th>Hazard</th><th>Issued at</th><th>P(event)</th><th>Outcome</th><th>Verdict</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const v = verdict(r)
            return (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td>{r.hazard}</td>
                <td className="mono">{r.issued_at}</td>
                <td>{(r.probability * 100).toFixed(0)}%{r.probability >= r.threshold ? ' ⚑' : ''}</td>
                <td>{r.occurred === null ? '—' : r.occurred ? 'occurred' : 'did not occur'}</td>
                <td><span className={`verdict ${v.cls}`}>{v.label}</span></td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <p className="honesty-note">
        ⚑ = the model would have raised an alert (P ≥ threshold). Each row's hash is
        SHA-256 over the previous hash + canonical payload; editing any record breaks
        the chain. The institutional trust gate is a real shadow season scored this
        way against a partner agency's live operations.
      </p>
    </div>
  )
}
