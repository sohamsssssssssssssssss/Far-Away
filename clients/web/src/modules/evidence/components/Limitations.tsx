import { AlertTriangle, ShieldAlert, Activity, Scale } from 'lucide-react'

// In-console honesty page. Every claim here is grounded in the reproducible
// validation output (docs/TECHNICAL_REPORT.md §6, regenerable via `make
// reproduce`). Foregrounding the limits is the point — a reviewer who sees the
// weaknesses stated plainly trusts the wins. Do not soften these.

interface Limit {
  title: string
  body: string
  severity: 'high' | 'medium'
}

const LIMITS: Limit[] = [
  {
    title: 'Earthquakes are assessed, not forecast',
    severity: 'high',
    body:
      'The earthquake module does rapid impact assessment for an already-detected ' +
      'event — it does not predict that a quake will occur. On its damage label it ' +
      'statistically ties the GMPE attenuation baseline (Δ −0.001, p = 0.64). The ' +
      'evacuation-lead framing does not apply to earthquakes and is not claimed for them.',
  },
  {
    title: 'High detection is bought with false alarms',
    severity: 'high',
    body:
      'To reach 90% detection the operating points accept high false-alarm ratios — ' +
      '0.87 (earthquake), 0.75 (flood), 0.63 (fire PNW). Over-warning erodes ' +
      'compliance (the cry-wolf effect, which the platform models explicitly), so ' +
      'these points are not free. India fire is the exception (FAR 0.37, CSI 0.60).',
  },
  {
    title: 'Long-lead flood warnings are imprecise',
    severity: 'medium',
    body:
      'Seven-day flood detection is real (POD ≈ 0.89 at 168 h) but pairs with an ' +
      '86% false-alarm ratio. The honest statement is "detects almost every flood a ' +
      'week out, at the cost of frequent false alarms," not "accurate 7 days out."',
  },
  {
    title: 'Regional generalisation is weaker than the headline',
    severity: 'medium',
    body:
      'Worst held-out-region AUC falls to ~0.80 for both fire models and 0.827 for ' +
      'earthquakes (Americas block). A deployment in an unseen region should expect ' +
      'the worst-block number, not the mean.',
  },
  {
    title: 'Labels are well-justified proxies',
    severity: 'medium',
    body:
      'Outcomes are discharge exceedance (flood), FIRMS detections (fire), and ' +
      'instrumental intensity / PAGER alerts (earthquake) — not surveyed losses. ' +
      'They are defensible proxies, but they are proxies.',
  },
  {
    title: 'The evacuation layer is uncalibrated',
    severity: 'high',
    body:
      'Clearance times, compliance rates, and casualty rates are explicit planning ' +
      'assumptions, not yet calibrated against agency ground truth. They are tunable, ' +
      'and the decision record states what was assumed.',
  },
  {
    title: 'No live shadow season has been completed yet',
    severity: 'high',
    body:
      'The shadow-mode harness and runner exist and are tested, but no live season ' +
      'has been collected and scored. Until one is, all evidence is retrospective. ' +
      'This is the decisive next step toward trusted operational use.',
  },
  {
    title: 'The model is intentionally simple',
    severity: 'medium',
    body:
      'One deterministic stdlib logistic regression per hazard — no gradient ' +
      'boosting, no network, no optional dependencies. A tuned model would likely ' +
      'score higher; the deliberate trade is reproducibility over peak accuracy.',
  },
]

const SEVERITY_META = {
  high: { label: 'MATERIAL', cls: 'limit-high', Icon: ShieldAlert },
  medium: { label: 'NOTED', cls: 'limit-medium', Icon: AlertTriangle },
} as const

export function Limitations() {
  return (
    <div className="evidence-pane">
      <div className="evidence-head">
        <h2>
          <Scale size={18} style={{ verticalAlign: '-3px', marginRight: 8 }} />
          Limitations &amp; Failure Modes
        </h2>
        <p className="evidence-sub">
          What this system does <em>not</em> do, and where it is weak — stated
          plainly. Every item is grounded in the reproducible validation output
          (regenerate with <code>make reproduce</code>; full write-up in
          <code> docs/TECHNICAL_REPORT.md</code> §6). This system is
          decision-support: a human commander holds authority over every
          consequential action.
        </p>
      </div>

      <div className="limits-grid">
        {LIMITS.map((l) => {
          const meta = SEVERITY_META[l.severity]
          const Icon = meta.Icon
          return (
            <div key={l.title} className={`limit-card ${meta.cls}`}>
              <div className="limit-card-head">
                <Icon size={16} />
                <span className="limit-badge">{meta.label}</span>
              </div>
              <h3 className="limit-title">{l.title}</h3>
              <p className="limit-body">{l.body}</p>
            </div>
          )
        })}
      </div>

      <div className="limit-footnote">
        <Activity size={14} style={{ verticalAlign: '-2px', marginRight: 6 }} />
        Honesty is a feature here: the validation suite excludes nothing, the
        shadow journal is tamper-evident, and the feed-status panel shows the real
        red/amber rather than an all-green mock.
      </div>
    </div>
  )
}
