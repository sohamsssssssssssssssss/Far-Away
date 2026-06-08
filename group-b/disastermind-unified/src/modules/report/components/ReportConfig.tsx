import { CheckCircle2, FileText, Users } from 'lucide-react'
import { audiences, incidents, reportSections, type Incident } from '../lib/incidents'

type ReportConfigProps = {
  selectedIncident: Incident
  checkedSections: string[]
  audience: string
  isGenerating: boolean
  onIncidentChange: (incident: Incident) => void
  onSectionToggle: (section: string) => void
  onAudienceChange: (audience: string) => void
  onGenerate: () => void
}

export default function ReportConfig({
  selectedIncident,
  checkedSections,
  audience,
  isGenerating,
  onIncidentChange,
  onSectionToggle,
  onAudienceChange,
  onGenerate,
}: ReportConfigProps) {
  return (
    <main className="config-page">
      <div className="config-wrap">
        <div className="title-block">
          <p className="kicker">POST-INCIDENT REPORT GENERATOR</p>
          <h1>Generate Incident Report</h1>
          <p>AI-synthesised report for government review</p>
        </div>

        <section className="config-panel">
          <div className="panel-heading">
            <FileText size={22} />
            <h2>Incident Selector</h2>
          </div>
          <div className="incident-list">
            {incidents.map((incident) => (
              <button
                className={`incident-card ${selectedIncident.id === incident.id ? 'selected' : ''}`}
                key={incident.id}
                onClick={() => onIncidentChange(incident)}
                type="button"
              >
                <div className="incident-title-row">
                  <h3>{incident.title}</h3>
                  {selectedIncident.id === incident.id && <CheckCircle2 size={20} />}
                </div>
                <p>
                  Duration: {incident.durationHours} hours | Zones: {incident.zones} | Teams deployed:{' '}
                  {incident.teamsDeployed}
                </p>
                <p>
                  Autonomous decisions: {incident.autonomousDecisions.toLocaleString('en-IN')} | Human overrides:{' '}
                  {incident.humanOverrides}
                </p>
                <strong>Civilians reached: {incident.civiliansReached.toLocaleString('en-IN')}</strong>
              </button>
            ))}
          </div>
        </section>

        <section className="config-panel two-column">
          <div>
            <div className="panel-heading">
              <CheckCircle2 size={22} />
              <h2>Report Sections</h2>
            </div>
            <div className="check-list">
              {reportSections.map((section) => (
                <label className="check-row" key={section}>
                  <input
                    checked={checkedSections.includes(section)}
                    onChange={() => onSectionToggle(section)}
                    type="checkbox"
                  />
                  <span>{section}</span>
                </label>
              ))}
            </div>
          </div>

          <div>
            <div className="panel-heading">
              <Users size={22} />
              <h2>Audience</h2>
            </div>
            <div className="radio-list">
              {audiences.map((option) => (
                <label className="radio-row" key={option.id}>
                  <input
                    checked={audience === option.id}
                    name="audience"
                    onChange={() => onAudienceChange(option.id)}
                    type="radio"
                  />
                  <span>
                    <strong>{option.label}</strong>
                    {option.detail}
                  </span>
                </label>
              ))}
            </div>
          </div>
        </section>

        <button className="generate-button" disabled={isGenerating || checkedSections.length === 0} onClick={onGenerate} type="button">
          {isGenerating ? 'GENERATING REPORT...' : 'GENERATE REPORT'}
        </button>
      </div>
    </main>
  )
}
