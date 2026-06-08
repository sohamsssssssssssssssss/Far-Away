import { AlertTriangle, Navigation, Shield, Users } from 'lucide-react'
import { useEffect, useState } from 'react'

export default function MapScreen() {
  const [toastVisible, setToastVisible] = useState(false)

  useEffect(() => {
    if (!toastVisible) return

    const timeout = window.setTimeout(() => setToastVisible(false), 1800)
    return () => window.clearTimeout(timeout)
  }, [toastVisible])

  return (
    <div className="map-screen">
      <div className="map-grid" />
      <div className="hazard-zone">FLOOD HAZARD</div>
      <div className="route-line" />

      <div className="map-marker you">
        <span className="dot cyan" />
        <strong>YOU</strong>
      </div>

      <div className="map-marker target">
        <MapPinVisual />
        <strong>ZONE 7 TARGET</strong>
      </div>

      <div className="map-marker team team-02">
        <span className="dot amber" />
        <strong>TEAM-02</strong>
      </div>
      <div className="map-marker team team-06">
        <span className="dot amber" />
        <strong>TEAM-06</strong>
      </div>

      <div className="map-marker shelter">
        <Shield size={20} />
        <strong>SHELTER - 73% FULL</strong>
      </div>

      <div className="map-marker risk">
        <AlertTriangle size={22} />
        <strong>STRUCTURAL RISK</strong>
      </div>

      <div className="map-marker unit-count">
        <Users size={18} />
        <strong>3 UNITS ACTIVE</strong>
      </div>

      <section className="map-action-card">
        <h2>ZONE 7 - BALASORE SECTOR</h2>
        <p>ETA: 11 MIN <span>•</span> 2.3 KM</p>
        <button type="button" className="primary-button" onClick={() => setToastVisible(true)}>
          <Navigation size={20} />
          NAVIGATE
        </button>
      </section>

      {toastVisible && <div className="toast">Opening Navigation...</div>}
    </div>
  )
}

function MapPinVisual() {
  return <span className="target-pin" aria-hidden="true" />
}
