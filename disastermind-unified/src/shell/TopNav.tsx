import { AlertTriangle, FileText, Monitor, Smartphone } from 'lucide-react'
import { useEffect, useState } from 'react'

export type UnifiedModuleKey = 'dashboard' | 'escalation' | 'field' | 'report'

type TopNavProps = {
  activeModule: UnifiedModuleKey
  onChange: (module: UnifiedModuleKey) => void
}

const modules: Array<{
  id: UnifiedModuleKey
  shortLabel: string
  Icon: typeof Monitor
}> = [
  { id: 'dashboard', shortLabel: 'COMMANDER', Icon: Monitor },
  { id: 'escalation', shortLabel: 'ESCALATION', Icon: AlertTriangle },
  { id: 'field', shortLabel: 'FIELD OPS', Icon: Smartphone },
  { id: 'report', shortLabel: 'INCIDENT REPORT', Icon: FileText },
]

const currentModuleName: Record<UnifiedModuleKey, string> = {
  dashboard: 'COMMANDER DASHBOARD',
  escalation: 'ESCALATION MEMO GENERATOR',
  field: 'FIELD TEAM INTERFACE',
  report: 'POST-INCIDENT REPORT',
}

const formatClock = (date: Date) =>
  date.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })

export function TopNav({ activeModule, onChange }: TopNavProps) {
  const [clock, setClock] = useState(() => formatClock(new Date()))

  useEffect(() => {
    const interval = window.setInterval(() => setClock(formatClock(new Date())), 1000)
    return () => window.clearInterval(interval)
  }, [])

  return (
    <header className="unified-top-nav">
      <div className="unified-branding">
        <strong>DISASTERMIND</strong>
        <span>//</span>
        <span>{currentModuleName[activeModule]}</span>
      </div>

      <nav className="unified-tabs" aria-label="DisasterMind modules">
        {modules.map(({ id, shortLabel, Icon }) => (
          <button
            key={id}
            type="button"
            className={`unified-tab ${activeModule === id ? 'active' : ''}`}
            onClick={() => onChange(id)}
            aria-pressed={activeModule === id}
          >
            <Icon size={16} strokeWidth={2.4} />
            <span>{shortLabel}</span>
          </button>
        ))}
      </nav>

      <div className="unified-status">
        <span className="unified-clock">{clock}</span>
        <span className="unified-badge">
          <i aria-hidden="true" />
          CYCLONE REMAL - ACTIVE
        </span>
      </div>
    </header>
  )
}
