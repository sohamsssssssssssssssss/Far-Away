import { useEffect, useState } from 'react'
import { Dashboard } from './modules/dashboard/Dashboard'
import { Escalation } from './modules/escalation/Escalation'
import { Field } from './modules/field/Field'
import { Report } from './modules/report/Report'
import { SplashScreen } from './shell/SplashScreen'
import { TopNav, type UnifiedModuleKey } from './shell/TopNav'
import { OfflineBanner } from './components/OfflineBanner'

function App() {
  const [activeModule, setActiveModule] = useState<UnifiedModuleKey>('dashboard')
  const [bootState, setBootState] = useState<'splash' | 'transition' | 'ready'>('splash')

  useEffect(() => {
    const splashTimer = window.setTimeout(() => {
      setBootState('transition')
      window.setTimeout(() => setBootState('ready'), 260)
    }, 2000)

    return () => window.clearTimeout(splashTimer)
  }, [])

  return (
    <div className="app-root">
      <div className={`app-shell ${bootState !== 'ready' ? 'booting' : ''}`}>
        <TopNav activeModule={activeModule} onChange={setActiveModule} />
        <main className="module-host">
          {activeModule === 'dashboard' && <Dashboard />}
          {activeModule === 'escalation' && <Escalation />}
          {activeModule === 'field' && <Field />}
          {activeModule === 'report' && <Report />}
        </main>
      </div>

      <SplashScreen visible={bootState !== 'ready'} />
      <OfflineBanner />
    </div>
  )
}

export default App
