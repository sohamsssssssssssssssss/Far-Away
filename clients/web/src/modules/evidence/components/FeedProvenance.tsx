import { AlertTriangle, CheckCircle2, KeyRound } from 'lucide-react'
import { FEED_ADAPTERS, PROVENANCE_SUMMARY } from '../feedProvenance'
import type { FeedStatus } from '../types'

const STATUS_META: Record<FeedStatus, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  live: { label: 'LIVE · KEY-FREE', cls: 'feed-live', Icon: CheckCircle2 },
  degraded: { label: 'NEEDS FIX', cls: 'feed-degraded', Icon: AlertTriangle },
  'key-required': { label: 'NEEDS KEY', cls: 'feed-key', Icon: KeyRound },
}

export function FeedProvenance() {
  return (
    <div className="evidence-pane">
      <div className="evidence-head">
        <h2>Live Feed Provenance</h2>
        <p className="evidence-sub">
          Direct reachability probe of every ingestion adapter against its real
          endpoint. The honest red/amber is the point — this is the actual status,
          not an all-green mock.
        </p>
      </div>

      <div className="feed-summary">
        <span className="feed-pill feed-live">{PROVENANCE_SUMMARY.liveKeyFree} live · key-free</span>
        <span className="feed-pill feed-degraded">{PROVENANCE_SUMMARY.degraded} need an endpoint/token fix</span>
        <span className="feed-pill feed-key">{PROVENANCE_SUMMARY.keyRequired} needs a provider key</span>
      </div>

      <div className="feed-grid">
        {FEED_ADAPTERS.map((a) => {
          const meta = STATUS_META[a.status]
          const Icon = meta.Icon
          return (
            <div key={a.name} className={`feed-card ${meta.cls}`}>
              <div className="feed-card-top">
                <Icon size={18} />
                <span className="feed-status-tag">{meta.label}</span>
              </div>
              <h3>{a.name}</h3>
              <code className="feed-endpoint">{a.source}{a.endpoint}</code>
              <p className="feed-detail">{a.detail}</p>
            </div>
          )
        })}
      </div>

      <p className="honesty-note">
        Probed live with anonymous GET requests. "Needs an endpoint/token fix" means
        the domain is reachable but the adapter's path has moved or now requires a
        token; "needs a provider key" means free-but-gated (registration required).
      </p>
    </div>
  )
}
