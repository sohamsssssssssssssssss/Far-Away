import { Ambulance, Bus, Cross, Helicopter, Sailboat, Shield } from 'lucide-react'

const resources = [
  { label: 'Boats', deployed: 12, total: 18, Icon: Sailboat },
  { label: 'Helicopters', deployed: 4, total: 6, Icon: Helicopter },
  { label: 'Medical Units', deployed: 9, total: 12, Icon: Cross },
  { label: 'Vehicles', deployed: 31, total: 46, Icon: Bus },
  { label: 'NDRF Teams', deployed: 14, total: 16, Icon: Shield },
]

const shelters = [
  { name: 'Puri Shelter A', current: 847, max: 1160 },
  { name: 'Balasore School', current: 522, max: 850 },
  { name: 'Cuttack Stadium', current: 1186, max: 1300 },
]

const capacityTone = (pct: number) => (pct > 90 ? 'danger' : pct >= 70 ? 'warning' : 'success')

export function ResourcePanel() {
  return (
    <section className="panel resource-panel">
      <div className="panel-title">
        <h2>RESOURCES</h2>
        <span>THEATRE ASSETS</span>
      </div>
      <div className="resource-list">
        {resources.map(({ label, deployed, total, Icon }) => {
          const pct = Math.round((deployed / total) * 100)
          return (
            <div className="resource-row" key={label}>
              <div className="resource-topline">
                <span className="resource-name"><Icon size={17} /> {label}</span>
                <span className="resource-count">{deployed} / {total}</span>
              </div>
              <div className="util-bar" aria-label={`${label} utilisation ${pct}%`}>
                <span style={{ width: `${pct}%` }} />
              </div>
            </div>
          )
        })}
      </div>

      <div className="shelter-section">
        <div className="subhead">
          <Ambulance size={15} />
          SHELTER CAPACITY
        </div>
        {shelters.map((shelter) => {
          const pct = Math.round((shelter.current / shelter.max) * 100)
          return (
            <div className={`shelter-card ${capacityTone(pct)}`} key={shelter.name}>
              <div>
                <strong>{shelter.name}</strong>
                <span>{shelter.current.toLocaleString('en-IN')} / {shelter.max.toLocaleString('en-IN')}</span>
              </div>
              <div className="capacity-meter" style={{ ['--capacity' as string]: `${pct * 3.6}deg` }}>
                <span>{pct}%</span>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
