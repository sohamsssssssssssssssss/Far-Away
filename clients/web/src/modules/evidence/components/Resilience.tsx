import { useCallback, useEffect, useState } from 'react'
import { Activity, RotateCcw } from 'lucide-react'
import { config } from '../../../lib/config'

interface DemoStatus {
  degraded_components: string[]
  operational: boolean
  mode: 'nominal' | 'degraded'
  known_components: string[]
}

// Friendly grouping/labels for the backend's known components.
const GROUPS: Array<{ title: string; ids: string[] }> = [
  { title: 'Data feeds', ids: ['usgs', 'imd', 'open-meteo', 'firms'] },
  { title: 'Broker & storage', ids: ['kafka', 'postgis', 'timescale', 'elasticsearch'] },
  { title: 'Models', ids: ['prediction', 'routing'] },
]
const LABEL: Record<string, string> = {
  usgs: 'USGS quakes', imd: 'IMD', 'open-meteo': 'Open-Meteo', firms: 'NASA FIRMS',
  kafka: 'Kafka broker', postgis: 'PostGIS', timescale: 'TimescaleDB',
  elasticsearch: 'Elasticsearch', prediction: 'Prediction', routing: 'Routing',
}

export function Resilience() {
  const [status, setStatus] = useState<DemoStatus | null>(null)
  const [offline, setOffline] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const base = config.api.baseUrl

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(`${base}/demo/status`, { signal: AbortSignal.timeout(5000) })
      if (!r.ok) throw new Error()
      setStatus(await r.json())
      setOffline(false)
    } catch {
      setOffline(true)
    }
  }, [base])

  useEffect(() => { void refresh() }, [refresh])

  async function toggle(component: string, active: boolean) {
    setBusy(component)
    try {
      const r = await fetch(`${base}/demo/degrade?component=${component}&active=${active}`, {
        method: 'POST', signal: AbortSignal.timeout(5000),
      })
      if (!r.ok) throw new Error()
      setStatus(await r.json()); setOffline(false)
    } catch {
      setOffline(true)
    } finally {
      setBusy(null)
    }
  }

  async function reset() {
    setBusy('reset')
    try {
      const r = await fetch(`${base}/demo/degrade?reset=true`, { method: 'POST', signal: AbortSignal.timeout(5000) })
      if (!r.ok) throw new Error()
      setStatus(await r.json()); setOffline(false)
    } catch { setOffline(true) } finally { setBusy(null) }
  }

  const degraded = new Set(status?.degraded_components ?? [])

  return (
    <div className="evidence-pane">
      <div className="evidence-head">
        <h2>System Resilience — Degraded-Mode Demo</h2>
        <p className="evidence-sub">
          DisasterMind is standard-library-first and degrades gracefully. Knock out
          a feed, the broker, storage, or a model and watch it stay
          <strong> operational</strong> — degraded ≠ down.
        </p>
      </div>

      {offline ? (
        <div className="resil-result resil-pending">
          <Activity size={16} />
          <span>
            Backend not reachable at <code>{base}</code>. Start the API
            (<code>python -m disastermind.api</code>) to drive this live demo — the
            degrade controls call the real <code>/demo/degrade</code> endpoint.
          </span>
        </div>
      ) : (
        <>
          <div className={`resil-status ${status?.mode === 'degraded' ? 'is-degraded' : 'is-nominal'}`}>
            <span className="resil-mode">{status?.mode === 'degraded' ? '⚠ DEGRADED MODE' : '● NOMINAL'}</span>
            <span className="resil-op">system {status?.operational ? 'OPERATIONAL ✓' : 'DOWN'}</span>
            {!!status?.degraded_components.length && (
              <span className="resil-deg">down: {status.degraded_components.map((c) => LABEL[c] ?? c).join(', ')}</span>
            )}
            <button className="resil-reset" disabled={busy !== null} onClick={reset}>
              <RotateCcw size={13} /> reset
            </button>
          </div>

          {GROUPS.map((g) => (
            <div key={g.title} className="resil-group">
              <h4>{g.title}</h4>
              <div className="resil-chips">
                {g.ids.map((id) => {
                  const isDown = degraded.has(id)
                  return (
                    <button
                      key={id}
                      className={`resil-chip ${isDown ? 'down' : 'up'}`}
                      disabled={busy !== null}
                      onClick={() => toggle(id, !isDown)}
                      title={isDown ? 'Click to restore' : 'Click to simulate failure'}
                    >
                      <i className="chip-dot" />
                      {LABEL[id] ?? id}
                      <span className="chip-state">{isDown ? 'DOWN' : 'up'}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </>
      )}

      <p className="honesty-note">
        This drives the real backend <code>POST /demo/degrade</code> endpoint and
        reflects live <code>/demo/status</code>. The toggle annotates components as
        simulated-down to demonstrate the fallbacks — it is honest and reversible
        and the system keeps coordinating throughout (that's the whole point).
      </p>
    </div>
  )
}
