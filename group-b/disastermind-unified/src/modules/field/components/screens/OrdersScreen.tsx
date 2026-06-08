import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  MapPin,
  ShipWheel,
  Users,
} from 'lucide-react'

const recentOrders = [
  'Zone 4 - Kendrapara: Cleared (06:12)',
  'Zone 5 - Jajpur: Cleared (05:44)',
]

export default function OrdersScreen() {
  return (
    <div className="screen orders-screen">
      <header className="top-bar">
        <div>
          <p className="eyebrow">TEAM-04 // NDRF ALPHA</p>
          <h1>ACTIVE ORDERS</h1>
        </div>
        <span className="badge danger">PRIORITY 1</span>
      </header>

      <section className="assignment-card">
        <div className="card-header">
          <span className="section-label">CURRENT ASSIGNMENT</span>
          <span className="status-pill pulse-warning">EN ROUTE</span>
        </div>
        <h2>ZONE 7 - BALASORE SECTOR</h2>
        <div className="mission-grid">
          <div>
            <span>MISSION</span>
            <strong>SEARCH & RESCUE</strong>
          </div>
          <div>
            <span>ETA</span>
            <strong className="mono hot">11 MIN</strong>
          </div>
        </div>
        <p className="waypoint">
          <MapPin size={18} />
          Vill. Chandpur, NH-16 junction, Balasore
        </p>
      </section>

      <section className="panel">
        <h3>ORDER DETAILS</h3>
        <div className="detail-row">
          <ShipWheel size={22} />
          <p>Deploy 2 boats at riverside entry point</p>
        </div>
        <div className="detail-row">
          <Users size={22} />
          <p>Estimated 40-60 survivors in 3 structures</p>
        </div>
        <div className="detail-row warning">
          <AlertTriangle size={22} />
          <p>Structural risk: 1 building flagged unstable</p>
        </div>
      </section>

      <section className="panel muted-panel">
        <h3>RECENT ORDERS</h3>
        {recentOrders.map((order) => (
          <div className="recent-order" key={order}>
            <div>
              <Clock size={16} />
              <span>{order}</span>
            </div>
            <span className="completed">
              COMPLETED <CheckCircle2 size={14} />
            </span>
          </div>
        ))}
      </section>
    </div>
  )
}
