import { useEffect, useState } from 'react'
import { RadioTower } from 'lucide-react'

const formatClock = (date: Date) =>
  date.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })

export function TopBar() {
  const [clock, setClock] = useState(() => formatClock(new Date()))

  useEffect(() => {
    const interval = window.setInterval(() => setClock(formatClock(new Date())), 1000)
    return () => window.clearInterval(interval)
  }, [])

  return (
    <header className="top-bar">
      <div className="top-cluster event-state">
        <span className="pulse-dot danger" aria-hidden="true" />
        <span>DISASTERMIND // ACTIVE EVENT</span>
      </div>
      <div className="top-event">
        <strong>CYCLONE REMAL - ODISHA COAST</strong>
        <span className="severity-badge">CAT 3</span>
      </div>
      <div className="top-cluster top-meta">
        <span className="clock">{clock}</span>
        <span>COMMANDER: COL. SHARMA</span>
        <span className="live-link">
          <RadioTower size={15} />
          LIVE <span className="live-dot">●</span>
        </span>
      </div>
    </header>
  )
}
