import { AlertOctagon, Minus, Plus, Send } from 'lucide-react'
import { useEffect, useState } from 'react'

const siteStatuses = ['EN ROUTE', 'ON SITE', 'SITE CLEARED', 'NEED SUPPORT']
const resourceOptions = ['+ BOAT', '+ MEDICAL UNIT', '+ HEAVY RESCUE']

export default function StatusScreen() {
  const [siteStatus, setSiteStatus] = useState('EN ROUTE')
  const [survivors, setSurvivors] = useState(0)
  const [resources, setResources] = useState<string[]>([])
  const [incidentFlagged, setIncidentFlagged] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    if (!submitted) return

    const timeout = window.setTimeout(() => setSubmitted(false), 2000)
    return () => window.clearTimeout(timeout)
  }, [submitted])

  const toggleResource = (resource: string) => {
    setResources((current) =>
      current.includes(resource)
        ? current.filter((item) => item !== resource)
        : [...current, resource],
    )
  }

  return (
    <div className="screen status-screen">
      <header className="stack-header">
        <p className="eyebrow">FIELD COORDINATION AGENT</p>
        <h1>REPORT STATUS</h1>
        <p>Updates sent to Field Coordination Agent</p>
      </header>

      <section className="panel">
        <h3>SITE STATUS</h3>
        <div className="status-grid">
          {siteStatuses.map((status) => (
            <button
              className={`tap-button ${siteStatus === status ? 'selected' : ''}`}
              key={status}
              onClick={() => setSiteStatus(status)}
              type="button"
            >
              {status}
            </button>
          ))}
        </div>
      </section>

      <section className="panel count-panel">
        <h3>SURVIVORS FOUND</h3>
        <div className="counter-row">
          <button
            aria-label="Decrease survivor count"
            className="stepper"
            onClick={() => setSurvivors((count) => Math.max(0, count - 1))}
            type="button"
          >
            <Minus size={28} />
          </button>
          <strong className="count-display">{survivors}</strong>
          <button
            aria-label="Increase survivor count"
            className="stepper"
            onClick={() => setSurvivors((count) => count + 1)}
            type="button"
          >
            <Plus size={28} />
          </button>
        </div>
      </section>

      <section className="panel">
        <h3>REQUEST ADDITIONAL RESOURCES</h3>
        <div className="chip-row">
          {resourceOptions.map((resource) => (
            <button
              className={`chip ${resources.includes(resource) ? 'active-warning' : ''}`}
              key={resource}
              onClick={() => toggleResource(resource)}
              type="button"
            >
              {resource}
            </button>
          ))}
        </div>
      </section>

      <section className={`incident-toggle ${incidentFlagged ? 'active' : ''}`}>
        <button type="button" onClick={() => setIncidentFlagged((value) => !value)}>
          <AlertOctagon size={24} />
          <span>FLAG INCIDENT (TEAM EMERGENCY)</span>
          <strong>{incidentFlagged ? 'ON' : 'OFF'}</strong>
        </button>
        {incidentFlagged && <p>This will alert all commanders</p>}
      </section>

      <button className={`submit-button ${submitted ? 'sent' : ''}`} onClick={() => setSubmitted(true)} type="button">
        <Send size={20} />
        {submitted ? 'SENT ✓' : 'SUBMIT STATUS REPORT'}
      </button>
    </div>
  )
}
