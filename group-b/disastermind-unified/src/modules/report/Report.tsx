import { useState } from 'react'
import ReportConfig from './components/ReportConfig'
import ReportViewer from './components/ReportViewer'
import {
  buildFallbackReport,
  generateReport,
  parseReport,
  type ReportSection,
} from './lib/anthropic'
import { incidents, reportSections, type Incident } from './lib/incidents'

export function Report() {
  const [view, setView] = useState<'config' | 'viewer'>('config')
  const [selectedIncident, setSelectedIncident] = useState<Incident>(incidents[0])
  const [checkedSections, setCheckedSections] = useState<string[]>(reportSections)
  const [audience, setAudience] = useState('SDMA')
  const [generatedAt, setGeneratedAt] = useState(new Date())
  const [isGenerating, setIsGenerating] = useState(false)
  const [sections, setSections] = useState<ReportSection[]>([])
  const [error, setError] = useState<string | null>(null)

  const toggleSection = (section: string) => {
    setCheckedSections((current) =>
      current.includes(section)
        ? current.filter((item) => item !== section)
        : [...current, section],
    )
  }

  const input = {
    incident: selectedIncident,
    sections: checkedSections,
    audience,
  }

  const handleGenerate = async () => {
    setView('viewer')
    setGeneratedAt(new Date())
    setIsGenerating(true)
    setError(null)
    setSections([])

    try {
      const text = await generateReport(input)
      setSections(parseReport(text))
    } catch (generationError) {
      const message =
        generationError instanceof Error
          ? `Local Ollama was unavailable or returned an error (${generationError.message}). Showing a generated fallback report for review.`
          : 'Local Ollama was unavailable. Showing a generated fallback report for review.'
      setError(message)
      setSections(parseReport(buildFallbackReport(input)))
    } finally {
      setIsGenerating(false)
    }
  }

  const handleNewReport = () => {
    setView('config')
    setError(null)
  }

  return (
    <div className="report-module">
      {view === 'config' ? (
        <ReportConfig
          audience={audience}
          checkedSections={checkedSections}
          isGenerating={isGenerating}
          onAudienceChange={setAudience}
          onGenerate={handleGenerate}
          onIncidentChange={setSelectedIncident}
          onSectionToggle={toggleSection}
          selectedIncident={selectedIncident}
        />
      ) : (
        <ReportViewer
          audience={audience}
          error={error}
          generatedAt={generatedAt}
          incident={selectedIncident}
          isLoading={isGenerating}
          onNewReport={handleNewReport}
          sections={sections}
        />
      )}
    </div>
  )
}
