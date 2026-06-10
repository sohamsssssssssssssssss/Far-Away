import type { IncidentReport } from '../services/reportService'

function formatDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('en-IN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function buildPrintableHtml(report: IncidentReport): string {
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>DisasterMind — Post-Incident Report ${report.incidentId}</title>
<style>
  @page { margin: 20mm 15mm; }
  body { font-family: 'Courier New', Courier, monospace; color: #1e293b; font-size: 11px; line-height: 1.5; padding: 20px; max-width: 800px; margin: 0 auto; }
  h1 { font-size: 18px; font-weight: 700; margin: 0 0 2px; letter-spacing: 1px; }
  .subtitle { font-size: 10px; color: #64748b; margin-bottom: 16px; }
  hr { border: none; border-top: 1px solid #cbd5e1; margin: 16px 0; }
  h2 { font-size: 12px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; color: #475569; margin: 16px 0 8px; }
  .grid-4 { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px; margin-bottom: 12px; }
  .stat-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 12px; text-align: center; }
  .stat-card .num { font-size: 22px; font-weight: 700; }
  .stat-card .lbl { font-size: 8px; text-transform: uppercase; letter-spacing: 0.5px; color: #64748b; margin-top: 2px; }
  .pill { display: inline-block; background: #f1f5f9; border-radius: 999px; padding: 2px 10px; font-size: 10px; font-family: monospace; margin: 2px; }
  .timeline { list-style: none; padding: 0; }
  .timeline li { display: flex; gap: 12px; padding: 6px 0; border-bottom: 1px solid #f1f5f9; }
  .timeline .time { font-weight: 700; font-size: 10px; min-width: 44px; color: #475569; }
  .timeline .detail { flex: 1; }
  .timeline .detail .event { font-weight: 600; }
  .timeline .detail .meta { font-size: 10px; color: #64748b; }
  .critical-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; margin-bottom: 8px; }
  .critical-card .num-badge { display: inline-block; background: #1e293b; color: #fff; border-radius: 50%; width: 20px; height: 20px; text-align: center; line-height: 20px; font-size: 10px; font-weight: 700; margin-right: 6px; }
  .critical-card .title { font-weight: 700; font-size: 11px; }
  .critical-card .rationale { color: #475569; font-size: 10px; margin-top: 2px; }
  .critical-card .impact { font-style: italic; font-size: 10px; color: #64748b; margin-top: 2px; }
  .lesson { border-left: 4px solid #f59e0b; padding-left: 10px; margin-bottom: 6px; font-size: 10px; }
  .rec { border-left: 4px solid #3b82f6; padding-left: 10px; margin-bottom: 6px; font-size: 10px; }
  .summary-text { font-size: 11px; color: #334155; line-height: 1.6; }
  .no-print { display: none; }
</style>
</head>
<body>
  <h1>DisasterMind — Post-Incident Report</h1>
  <div class="subtitle">${report.incidentId} &middot; Generated ${formatDate(report.generatedAt)}</div>
  <hr>

  <h2>Executive Summary</h2>
  <p class="summary-text">${report.summary}</p>

  <h2>Metrics</h2>
  <div class="grid-4">
    <div class="stat-card"><div class="num">${report.escalationsRaised}</div><div class="lbl">Raised</div></div>
    <div class="stat-card"><div class="num">${report.escalationsApproved}</div><div class="lbl">Approved</div></div>
    <div class="stat-card"><div class="num">${report.escalationsRejected}</div><div class="lbl">Rejected</div></div>
    <div class="stat-card"><div class="num">${report.overridesIssued}</div><div class="lbl">Overrides</div></div>
  </div>

  <h2>Agents Deployed</h2>
  <div>${report.agentsDeployed.map(a => `<span class="pill">${a}</span>`).join(' ')}</div>

  <h2>Decision Timeline</h2>
  <ul class="timeline">
    ${report.timeline.map(t => `
      <li>
        <span class="time">${t.time}</span>
        <div class="detail">
          <div class="event">${t.event}</div>
          <div class="meta">${t.actor} &middot; ${t.outcome}</div>
        </div>
      </li>
    `).join('')}
  </ul>

  <h2>Critical Decisions</h2>
  ${report.criticalDecisions.map((d, i) => `
    <div class="critical-card">
      <div><span class="num-badge">${i + 1}</span><span class="title">${d.decision}</span></div>
      <div class="rationale">${d.rationale}</div>
      <div class="impact">${d.impact}</div>
    </div>
  `).join('')}

  <h2>Lessons Learned</h2>
  ${report.lessonsLearned.map(l => `<div class="lesson">${l}</div>`).join('')}

  <h2>Recommendations</h2>
  ${report.recommendations.map((r, i) => `<div class="rec">${i + 1}. ${r}</div>`).join('')}
</body>
</html>`
}

export async function exportReportToPdf(report: IncidentReport): Promise<void> {
  const html = buildPrintableHtml(report)
  const blob = new Blob([html], { type: 'text/html' })
  const url = URL.createObjectURL(blob)

  const printWindow = window.open(url, '_blank')
  if (printWindow) {
    printWindow.onload = () => {
      printWindow.print()
      // Clean up after print dialog closes (or user cancels)
      setTimeout(() => {
        URL.revokeObjectURL(url)
        printWindow.close()
      }, 1000)
    }
  } else {
    // Fallback: if popup blocked, inject a hidden iframe
    const iframe = document.createElement('iframe')
    iframe.style.position = 'fixed'
    iframe.style.top = '-9999px'
    iframe.style.left = '-9999px'
    iframe.style.width = '0'
    iframe.style.height = '0'
    document.body.appendChild(iframe)

    iframe.contentDocument?.write(html)
    iframe.contentDocument?.close()

    iframe.onload = () => {
      iframe.contentWindow?.print()
      setTimeout(() => {
        document.body.removeChild(iframe)
        URL.revokeObjectURL(url)
      }, 1000)
    }
  }
}
