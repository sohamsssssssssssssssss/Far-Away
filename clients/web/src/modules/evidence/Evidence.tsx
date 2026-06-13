import { useState } from 'react'
import { Activity, Radio, ScrollText, Scale, Wind } from 'lucide-react'
import './evidence.css'
import { CycloneBacktestMap } from './components/CycloneBacktestMap'
import { FeedProvenance } from './components/FeedProvenance'
import { ShadowJournal } from './components/ShadowJournal'
import { Resilience } from './components/Resilience'
import { Limitations } from './components/Limitations'

type EvidenceTab = 'cyclone' | 'feeds' | 'shadow' | 'resilience' | 'limits'

const TABS: Array<{ id: EvidenceTab; label: string; Icon: typeof Wind }> = [
  { id: 'cyclone', label: 'Cyclone Backtest', Icon: Wind },
  { id: 'feeds', label: 'Feed Status', Icon: Radio },
  { id: 'shadow', label: 'Shadow Journal', Icon: ScrollText },
  { id: 'resilience', label: 'Resilience', Icon: Activity },
  { id: 'limits', label: 'Limitations', Icon: Scale },
]

export function Evidence() {
  const [tab, setTab] = useState<EvidenceTab>('cyclone')

  return (
    <div className="evidence-module">
      <nav className="evidence-tabs" aria-label="Evidence views">
        {TABS.map((t) => {
          const Icon = t.Icon
          return (
            <button
              key={t.id}
              className={`evidence-tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              <Icon size={15} />
              {t.label}
            </button>
          )
        })}
      </nav>

      <div className="evidence-content">
        {tab === 'cyclone' && <CycloneBacktestMap />}
        {tab === 'feeds' && <FeedProvenance />}
        {tab === 'shadow' && <ShadowJournal />}
        {tab === 'resilience' && <Resilience />}
        {tab === 'limits' && <Limitations />}
      </div>
    </div>
  )
}
