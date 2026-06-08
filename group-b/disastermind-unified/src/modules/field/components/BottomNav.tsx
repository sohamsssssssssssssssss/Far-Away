import { ClipboardList, Map, MessageSquare, Radio } from 'lucide-react'
import type { Screen } from '../Field'

type BottomNavProps = {
  activeScreen: Screen
  onChange: (screen: Screen) => void
}

const tabs: Array<{
  id: Screen
  label: string
  Icon: typeof ClipboardList
}> = [
  { id: 'orders', label: 'ORDERS', Icon: ClipboardList },
  { id: 'map', label: 'MAP', Icon: Map },
  { id: 'status', label: 'STATUS', Icon: Radio },
  { id: 'comms', label: 'COMMS', Icon: MessageSquare },
]

export default function BottomNav({ activeScreen, onChange }: BottomNavProps) {
  return (
    <nav className="bottom-nav" aria-label="Field app navigation">
      {tabs.map(({ id, label, Icon }) => (
        <button
          className={`nav-tab ${activeScreen === id ? 'active' : ''}`}
          key={id}
          onClick={() => onChange(id)}
          type="button"
        >
          <Icon size={22} strokeWidth={2.4} />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  )
}
