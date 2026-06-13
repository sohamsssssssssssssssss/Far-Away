import { useEffect, useState } from 'react'
import { useIsMobile } from './hooks/useIsMobile'
import { Dashboard } from './modules/dashboard/Dashboard'
import { Escalation } from './modules/escalation/Escalation'
import { Field } from './modules/field/Field'
import { Report } from './modules/report/Report'
import { Evidence } from './modules/evidence/Evidence'
import { SplashScreen } from './shell/SplashScreen'
import { CommandShell } from './shell/CommandShell'
import type { UnifiedModuleKey } from './shell/TopNav'
import { OfflineBanner } from './components/OfflineBanner'

function App() {
  const isMobile = useIsMobile()
  const [activeModule, setActiveModule] = useState<UnifiedModuleKey>(isMobile ? 'field' : 'dashboard')
  const [bootState, setBootState] = useState<'splash' | 'transition' | 'ready'>('splash')

  useEffect(() => {
    const splashTimer = window.setTimeout(() => {
      setBootState('transition')
      window.setTimeout(() => setBootState('ready'), 260)
    }, 2000)
    return () => window.clearTimeout(splashTimer)
  }, [])

  // Opt the document into the light "tactical sand" surface.
  useEffect(() => {
    document.body.classList.add('dm-light')
    return () => document.body.classList.remove('dm-light')
  }, [])

  // The field interface is a self-contained full-screen mobile app.
  if (isMobile) {
    return (
      <>
        <Field />
        <SplashScreen visible={bootState !== 'ready'} />
        <OfflineBanner />
      </>
    )
  }

  return (
    <>
      <CommandShell activeModule={activeModule} onChange={setActiveModule}>
        {activeModule === 'dashboard' && <Dashboard />}
        {activeModule === 'escalation' && <Escalation />}
        {activeModule === 'report' && <Report />}
        {activeModule === 'evidence' && <Evidence />}
        {activeModule === 'field' && <Field />}
      </CommandShell>

      <SplashScreen visible={bootState !== 'ready'} />
      <OfflineBanner />
    </>
  )
}

export default App
