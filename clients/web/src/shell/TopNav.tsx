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
  const [shouldFlashEscalation, setShouldFlashEscalation] = useState(false)
  const [badgeState, setBadgeState] = useState<'red' | 'amber'>('red')

  useEffect(() => {
    const interval = window.setInterval(() => setClock(formatClock(new Date())), 1000)
    return () => window.clearInterval(interval)
  }, [])

  useEffect(() => {
    let timeoutId: number;
    const handleFlash = () => {
      setShouldFlashEscalation(true)
      timeoutId = window.setTimeout(() => setShouldFlashEscalation(false), 3000)
    }

    const handleAmber = () => {
      setBadgeState('amber')
    }

    const handleRed = () => {
      setBadgeState('red')
    }

    window.addEventListener('flash-escalation-tab', handleFlash)
    window.addEventListener('cyclone-badge-amber', handleAmber)
    window.addEventListener('cyclone-badge-red', handleRed)

    return () => {
      window.clearTimeout(timeoutId)
      window.removeEventListener('flash-escalation-tab', handleFlash)
      window.removeEventListener('cyclone-badge-amber', handleAmber)
      window.removeEventListener('cyclone-badge-red', handleRed)
    }
  }, [])

  return (
    <header className="unified-top-nav">
      <style>{`
        @keyframes flash-tab-red {
          0%, 100% { background: transparent; color: var(--text-secondary); border-color: transparent; }
          50% { background: rgba(255, 59, 59, 0.25); color: #ff3b3b; border-color: rgba(255, 59, 59, 0.5); }
        }
        .flash-tab-red-anim {
          animation: flash-tab-red 0.5s ease-in-out 6;
        }
        .unified-badge.amber {
          background: rgba(255, 170, 0, .12) !important;
          color: #ffaa00 !important;
        }
        .unified-badge.amber i {
          background: #ffaa00 !important;
          box-shadow: 0 0 8px #ffaa00 !important;
        }
      `}</style>
      <div className="unified-branding">
        <strong>DISASTERMIND</strong>
        <span>//</span>
        <span>{currentModuleName[activeModule]}</span>
      </div>

      <nav className="unified-tabs" aria-label="DisasterMind modules">
        {modules.map(({ id, shortLabel, Icon }) => {
          const isEscalationTab = id === 'escalation'
          const tabClass = `unified-tab ${activeModule === id ? 'active' : ''} ${isEscalationTab && shouldFlashEscalation ? 'flash-tab-red-anim' : ''}`
          return (
            <button
              key={id}
              type="button"
              className={tabClass}
              onClick={() => onChange(id)}
              aria-pressed={activeModule === id}
            >
              <Icon size={16} strokeWidth={2.4} />
              <span>{shortLabel}</span>
            </button>
          )
        })}
      </nav>

      <div className="unified-status">
        <span className="unified-clock">{clock}</span>
        <span className={`unified-badge ${badgeState === 'amber' ? 'amber' : ''}`}>
          <i aria-hidden="true" />
          CYCLONE REMAL - ACTIVE
        </span>
      </div>
    </header>
  )
}
