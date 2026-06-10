import type { ShapFeature, AgentDecisionShap } from '../lib/mapTypes'

interface ShapBadgesProps {
  shap: AgentDecisionShap
}

function FeaturePill({ feature }: { feature: ShapFeature }) {
  const isUp = feature.direction === 'up'
  const arrow = isUp ? '↑' : '↓'
  const color = isUp ? '#ef4444' : '#22c55e'
  const bg = isUp ? 'rgba(239,68,68,0.1)' : 'rgba(34,197,94,0.1)'
  const border = isUp ? 'rgba(239,68,68,0.25)' : 'rgba(34,197,94,0.25)'

  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '3px',
      padding: '2px 7px',
      fontSize: '10px',
      fontWeight: 600,
      color,
      background: bg,
      border: `1px solid ${border}`,
      borderRadius: '3px',
      whiteSpace: 'nowrap',
    }}>
      <span style={{ fontSize: '9px' }}>{arrow}</span>
      {feature.label}
    </span>
  )
}

export function ShapBadges({ shap }: ShapBadgesProps) {
  const confidencePct = Math.round(shap.modelConfidence * 100)
  const confColor = shap.modelConfidence >= 0.9 ? '#22c55e'
    : shap.modelConfidence >= 0.75 ? '#f59e0b'
    : '#ef4444'

  return (
    <div style={{ marginTop: '10px', borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: '8px' }}>
      {/* Label row */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '6px',
      }}>
        <span style={{
          fontSize: '9px',
          fontWeight: 700,
          letterSpacing: '0.1em',
          color: '#475569',
        }}>
          MODEL DRIVERS
        </span>
        <span style={{
          fontSize: '9px',
          fontWeight: 700,
          color: confColor,
          letterSpacing: '0.05em',
        }}>
          {confidencePct}% CONF
        </span>
      </div>

      {/* Feature pills */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
        {shap.topFeatures.map((f, i) => (
          <FeaturePill key={i} feature={f} />
        ))}
      </div>
    </div>
  )
}
