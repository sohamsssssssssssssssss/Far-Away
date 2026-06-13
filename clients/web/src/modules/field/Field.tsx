import { useState } from 'react'
import './field.css'
import BottomNav from './components/BottomNav'
import CommsScreen from './components/screens/CommsScreen'
import MapScreen from './components/screens/MapScreen'
import OrdersScreen from './components/screens/OrdersScreen'
import StatusScreen from './components/screens/StatusScreen'

export type Screen = 'orders' | 'map' | 'status' | 'comms'

export function Field() {
  const [activeScreen, setActiveScreen] = useState<Screen>('orders')

  const renderScreen = () => {
    switch (activeScreen) {
      case 'map':
        return <MapScreen />
      case 'status':
        return <StatusScreen />
      case 'comms':
        return <CommsScreen />
      default:
        return <OrdersScreen />
    }
  }

  return (
    <main className="field-module">
      <div className="field-stage">
        <p className="field-label">FIELD TEAM INTERFACE - TEAM-04 // NDRF ALPHA</p>
        <section className="phone-frame" aria-label="DisasterMind field team app">
          <div className="screen-shell">{renderScreen()}</div>
          <BottomNav activeScreen={activeScreen} onChange={setActiveScreen} />
        </section>
      </div>
    </main>
  )
}
