import { useEffect, useState } from 'react'
import type { UnifiedModuleKey } from './TopNav'
import { Icon } from '@/components/ui/icon'
import { cn } from '@/lib/utils'

interface NavItem {
  key: UnifiedModuleKey
  label: string
  icon: string
}

const NAV_ITEMS: NavItem[] = [
  { key: 'dashboard', label: 'Dashboard', icon: 'dashboard' },
  { key: 'escalation', label: 'Escalations', icon: 'warning' },
  { key: 'report', label: 'Incidents', icon: 'emergency' },
  { key: 'evidence', label: 'Evidence', icon: 'fact_check' },
]

const formatClock = (date: Date) =>
  date.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })

interface CommandShellProps {
  activeModule: UnifiedModuleKey
  onChange: (module: UnifiedModuleKey) => void
  children: React.ReactNode
}

export function CommandShell({ activeModule, onChange, children }: CommandShellProps) {
  const [clock, setClock] = useState(() => formatClock(new Date()))
  const [cyclone, setCyclone] = useState<'red' | 'amber'>('red')
  const [flashEscalation, setFlashEscalation] = useState(false)

  useEffect(() => {
    const t = window.setInterval(() => setClock(formatClock(new Date())), 1000)
    return () => window.clearInterval(t)
  }, [])

  useEffect(() => {
    const onAmber = () => setCyclone('amber')
    const onRed = () => setCyclone('red')
    let timeout: number
    const onFlash = () => {
      setFlashEscalation(true)
      timeout = window.setTimeout(() => setFlashEscalation(false), 3000)
    }
    window.addEventListener('cyclone-badge-amber', onAmber)
    window.addEventListener('cyclone-badge-red', onRed)
    window.addEventListener('flash-escalation-tab', onFlash)
    return () => {
      window.clearTimeout(timeout)
      window.removeEventListener('cyclone-badge-amber', onAmber)
      window.removeEventListener('cyclone-badge-red', onRed)
      window.removeEventListener('flash-escalation-tab', onFlash)
    }
  }, [])

  return (
    <div className="h-screen w-full overflow-hidden bg-surface text-on-surface font-sans">
      {/* TopAppBar */}
      <header className="fixed top-0 inset-x-0 z-50 flex h-16 items-center justify-between border-b border-outline-variant/15 bg-surface px-margin-desktop">
        <div className="flex items-center gap-3">
          <Icon name="shield" filled className="text-[26px] text-primary" />
          <span className="text-headline-md text-primary">DisasterMind</span>
          <span className="hidden text-outline md:inline">/</span>
          <span className="hidden text-label-md uppercase text-on-surface-variant md:inline">
            Sector 7 Command
          </span>
        </div>
        <div className="flex items-center gap-5">
          <span className="hidden text-data-mono tabular-nums text-on-surface-variant sm:inline">
            {clock} IST
          </span>
          <span
            className={cn(
              'hidden items-center gap-2 rounded border px-2.5 py-1 text-label-sm uppercase lg:inline-flex',
              cyclone === 'red'
                ? 'border-error/25 bg-error/10 text-error'
                : 'border-on-tertiary-container/25 bg-on-tertiary-container/10 text-on-tertiary-container',
            )}
          >
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                cyclone === 'red' ? 'animate-pulse bg-error' : 'bg-on-tertiary-container',
              )}
            />
            Cyclone Remal — Active
          </span>
          <button
            type="button"
            className="flex h-10 w-10 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container-high active:opacity-80"
            aria-label="Notifications"
          >
            <Icon name="notifications" />
          </button>
          <div className="flex h-9 w-9 items-center justify-center rounded-full border border-outline-variant/30 bg-surface-variant text-label-md text-on-surface-variant">
            C7
          </div>
        </div>
      </header>

      {/* SideNavBar */}
      <nav className="fixed left-0 top-0 z-40 hidden h-full w-64 flex-col border-r border-outline-variant/10 bg-surface-container-low pb-8 pt-20 md:flex">
        <div className="mb-8 flex items-center gap-3 px-6">
          <div className="flex h-10 w-10 items-center justify-center rounded bg-primary-container text-on-primary">
            <Icon name="radar" />
          </div>
          <div>
            <div className="text-headline-sm text-primary">HQ-Alpha</div>
            <div className="text-label-sm uppercase text-on-surface-variant">Sector 7 Admin</div>
          </div>
        </div>

        <div className="flex-1 space-y-1 px-4">
          {NAV_ITEMS.map((item) => {
            const isActive = item.key === activeModule
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => onChange(item.key)}
                className={cn(
                  'flex w-full items-center gap-3 rounded-lg px-4 py-2.5 text-label-md transition-all duration-150 active:scale-[0.98]',
                  isActive
                    ? 'border-r-4 border-tertiary-container bg-surface-container-high font-bold text-primary'
                    : 'text-secondary hover:bg-surface-container-highest',
                  item.key === 'escalation' && flashEscalation && 'animate-pulse bg-error/10 text-error',
                )}
              >
                <Icon name={item.icon} filled={isActive} className="text-[20px]" />
                <span>{item.label}</span>
              </button>
            )
          })}
        </div>

        <div className="space-y-1 px-4">
          {[
            { label: 'Settings', icon: 'settings' },
            { label: 'Support', icon: 'help' },
          ].map((item) => (
            <button
              key={item.label}
              type="button"
              className="flex w-full items-center gap-3 rounded-lg px-4 py-2.5 text-label-md text-secondary transition-colors hover:bg-surface-container-highest"
            >
              <Icon name={item.icon} className="text-[20px]" />
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      </nav>

      {/* Canvas */}
      <main className="h-[calc(100vh-4rem)] overflow-hidden pt-16 md:pl-64">{children}</main>
    </div>
  )
}
