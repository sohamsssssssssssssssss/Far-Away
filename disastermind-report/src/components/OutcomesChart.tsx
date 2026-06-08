type OutcomesChartProps = {
  atRisk: number
  reached: number
}

export default function OutcomesChart({ atRisk, reached }: OutcomesChartProps) {
  const percentage = (reached / atRisk) * 100

  return (
    <section className="chart-card outcome-card">
      <div className="outcome-header">
        <h2>Civilians Reached vs At Risk</h2>
        <strong>{percentage.toFixed(1)}%</strong>
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${percentage}%` }} />
      </div>
      <div className="outcome-grid">
        <div>
          <span>AT RISK</span>
          <strong>{atRisk.toLocaleString('en-IN')}</strong>
        </div>
        <div>
          <span>REACHED</span>
          <strong>{reached.toLocaleString('en-IN')}</strong>
        </div>
      </div>
    </section>
  )
}
