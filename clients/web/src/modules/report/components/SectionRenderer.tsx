import type { ReportSection } from '../lib/anthropic'

type SectionRendererProps = {
  section: ReportSection
}

export default function SectionRenderer({ section }: SectionRendererProps) {
  return (
    <section className="report-section">
      <h2>{section.title}</h2>
      <div className="section-rule" />
      {section.body.split(/\n+/).map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
    </section>
  )
}
