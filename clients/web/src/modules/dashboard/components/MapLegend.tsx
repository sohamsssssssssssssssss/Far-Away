export function MapLegend() {
  return (
    <div style={{
      position: 'absolute',
      bottom: '12px',
      left: '12px',
      background: 'rgba(0,0,0,0.75)',
      border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: '6px',
      padding: '8px 12px',
      fontSize: '11px',
      color: '#e2e8f0',
      zIndex: 10,
      pointerEvents: 'none',
      display: 'flex',
      flexDirection: 'column',
      gap: '4px',
    }}>
      <div style={{ fontWeight: 700, marginBottom: '4px', fontSize: '10px', letterSpacing: '0.08em', color: '#94a3b8' }}>
        MAP LEGEND
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#22c55e', display: 'inline-block' }} />
        Active team
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#f59e0b', display: 'inline-block' }} />
        Staged
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#ef4444', display: 'inline-block' }} />
        Distress
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{ width: 20, height: 2, background: '#f59e0b', display: 'inline-block' }} />
        Evacuation route
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{ width: 20, height: 2, background: '#3b82f6', display: 'inline-block' }} />
        Rescue route
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{ width: 20, height: 8, background: 'linear-gradient(to right, rgba(0,255,255,0.5), rgba(255,0,0,0.9))', display: 'inline-block', borderRadius: 2 }} />
        Risk heatmap
      </div>
      {/* Shelters */}
      <div style={{ marginTop: '8px', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '8px' }}>
        <div style={{ fontSize: '8px', letterSpacing: '0.1em', color: '#475569', marginBottom: '4px' }}>SHELTERS</div>
        {[
          { color: '#22c55e', label: 'Open' },
          { color: '#f97316', label: '>80% full' },
          { color: '#ef4444', label: 'Full' },
          { color: '#475569', label: 'Closed' },
        ].map(({ color, label }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
            <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: color, flexShrink: 0 }} />
            <span style={{ fontSize: '9px', color: '#94a3b8' }}>{label}</span>
          </div>
        ))}
      </div>
      {/* IMD Alerts */}
      <div style={{ marginTop: '8px', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '8px' }}>
        <div style={{ fontSize: '8px', letterSpacing: '0.1em', color: '#475569', marginBottom: '4px' }}>IMD ALERTS</div>
        {[
          { color: '#ef4444', label: 'Red — Extreme' },
          { color: '#f97316', label: 'Orange — Severe' },
          { color: '#eab308', label: 'Yellow — Watch' },
        ].map(({ color, label }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
            <div style={{ width: '10px', height: '6px', borderRadius: '2px', background: color, opacity: 0.7, flexShrink: 0 }} />
            <span style={{ fontSize: '9px', color: '#94a3b8' }}>{label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
