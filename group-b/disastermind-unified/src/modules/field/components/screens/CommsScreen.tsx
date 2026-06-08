import { RadioTower, Send, Siren, X } from 'lucide-react'
import { useEffect, useState } from 'react'

type Message = {
  sender: string
  time: string
  body: string
  kind: 'command' | 'team'
}

const initialMessages: Message[] = [
  {
    sender: 'COMMAND',
    time: '06:41',
    body: 'TEAM-04, redirect to Zone 7. Elevated risk. Proceed immediately.',
    kind: 'command',
  },
  {
    sender: 'TEAM-02',
    time: '06:39',
    body: 'Zone 5 cleared. 23 survivors evacuated. En route to staging.',
    kind: 'team',
  },
  {
    sender: 'COMMAND',
    time: '06:35',
    body: 'River gauge Mahanadi rising rapidly. Prioritise Zone 7.',
    kind: 'command',
  },
  {
    sender: 'TEAM-06',
    time: '06:30',
    body: 'Requesting medical support at Balasore shelter. 2 critical.',
    kind: 'team',
  },
]

const quickReplies = ['ACKNOWLEDGED', 'EN ROUTE', 'ON SITE', 'NEED SUPPORT']

export default function CommsScreen() {
  const [messages, setMessages] = useState<Message[]>(initialMessages)
  const [modalOpen, setModalOpen] = useState(false)
  const [distressSent, setDistressSent] = useState(false)
  const [flash, setFlash] = useState(false)

  useEffect(() => {
    if (!flash) return

    const timeout = window.setTimeout(() => setFlash(false), 650)
    return () => window.clearTimeout(timeout)
  }, [flash])

  const sendQuickReply = (reply: string) => {
    setMessages((current) => [
      {
        sender: 'TEAM-04',
        time: 'NOW',
        body: reply,
        kind: 'team',
      },
      ...current,
    ])
  }

  const confirmDistress = () => {
    setModalOpen(false)
    setDistressSent(true)
    setFlash(true)
  }

  return (
    <div className={`screen comms-screen ${distressSent ? 'distress-mode' : ''}`}>
      <header className="signal-bar">
        <RadioTower size={18} />
        <span>MOBILE SIGNAL ●●●○○ | SATELLITE: STANDBY</span>
      </header>

      {distressSent && (
        <section className="distress-banner">
          <Siren size={28} />
          <strong>DISTRESS SIGNAL SENT</strong>
        </section>
      )}

      <section className="messages-panel">
        <h1>COMMS</h1>
        <div className="message-list" aria-live="polite">
          {messages.map((message, index) => (
            <article className={`message ${message.kind}`} key={`${message.sender}-${message.time}-${index}`}>
              <strong>
                {message.sender} - {message.time}
              </strong>
              <p>{message.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="quick-replies">
        <div className="chip-row">
          {quickReplies.map((reply) => (
            <button className="chip comms-chip" key={reply} onClick={() => sendQuickReply(reply)} type="button">
              <Send size={14} />
              {reply}
            </button>
          ))}
        </div>
      </section>

      <section className="emergency-panel">
        <button className="distress-button" onClick={() => setModalOpen(true)} type="button">
          <Siren size={28} />
          DISTRESS SIGNAL
        </button>
        <p>Broadcasts to ALL commanders and agents</p>
      </section>

      {modalOpen && (
        <div className="modal-backdrop" role="presentation">
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="distress-title">
            <button className="modal-close" aria-label="Cancel distress signal" onClick={() => setModalOpen(false)} type="button">
              <X size={22} />
            </button>
            <Siren className="modal-icon" size={34} />
            <h2 id="distress-title">CONFIRM DISTRESS SIGNAL?</h2>
            <p>This will alert all commanders and reroute nearest support to your location.</p>
            <button className="confirm-distress" onClick={confirmDistress} type="button">
              CONFIRM - SEND DISTRESS
            </button>
            <button className="cancel-button" onClick={() => setModalOpen(false)} type="button">
              CANCEL
            </button>
          </section>
        </div>
      )}

      {flash && <div className="red-flash" />}
    </div>
  )
}
