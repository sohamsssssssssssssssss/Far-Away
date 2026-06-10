import { useState, useEffect, useCallback } from 'react'
import { generateReport } from '../services/reportService'
import type { IncidentReport, AuditEntry } from '../services/reportService'
import { exportReportToPdf } from '../utils/exportPdf'

interface PostIncidentReportProps {
  auditLog: AuditEntry[]
  onClose: () => void
}

type LoadState = 'idle' | 'loading' | 'success' | 'error'

export function PostIncidentReport({ auditLog, onClose }: PostIncidentReportProps) {
  const [state, setState] = useState<LoadState>('idle')
  const [report, setReport] = useState<IncidentReport | null>(null)
  const [exporting, setExporting] = useState(false)

  const loadReport = useCallback(async () => {
    setState('loading')
    try {
      const result = await generateReport(auditLog)
      setReport(result)
      setState('success')
    } catch (err) {
      setState('error')
      console.error('[PostIncidentReport] Generation failed:', err)
    }
  }, [auditLog])

  useEffect(() => {
    loadReport()
  }, [loadReport])

  // Escape key closes
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const handleExportPdf = async () => {
    if (!report) return
    setExporting(true)
    try {
      await exportReportToPdf(report)
    } catch (err) {
      console.error('[PostIncidentReport] PDF export failed:', err)
    } finally {
      setExporting(false)
    }
  }

  const isEmpty = auditLog.length === 0

  return (
    <>
      {/* Backdrop */}
      <div
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.6)',
          zIndex: 998,
          animation: 'fadeIn 0.15s ease',
        }}
        onClick={onClose}
      />

      {/* Modal */}
      <div
        style={{
          position: 'fixed',
          inset: '40px',
          maxWidth: '900px',
          margin: '0 auto',
          background: '#ffffff',
          borderRadius: '12px',
          zIndex: 999,
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 12px 48px rgba(0,0,0,0.3)',
          animation: 'fadeIn 0.2s ease',
          overflow: 'hidden',
          color: '#1e293b',
        }}
      >
        {/* Header bar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 20px',
            borderBottom: '1px solid #e2e8f0',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.12em', color: '#94a3b8', textTransform: 'uppercase' }}>
              Post-Incident Report
            </span>
            {report && (
              <span style={{ fontSize: '18px', fontWeight: 700, fontFamily: 'monospace', color: '#0f172a' }}>
                {report.incidentId}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              fontSize: '18px',
              color: '#94a3b8',
              cursor: 'pointer',
              padding: '4px 8px',
              lineHeight: 1,
              borderRadius: '4px',
            }}
          >
            ✕
          </button>
        </div>

        {/* Scrollable body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
          {/* EMPTY STATE */}
          {isEmpty && (
            <div style={{ textAlign: 'center', padding: '60px 20px', color: '#94a3b8' }}>
              <div style={{ fontSize: '40px', marginBottom: '12px' }}>📋</div>
              <p style={{ fontSize: '13px', fontWeight: 600 }}>No incident activity recorded yet</p>
              <p style={{ fontSize: '11px', marginTop: '4px' }}>Run an operation first to generate a report.</p>
            </div>
          )}

          {/* LOADING STATE */}
          {state === 'loading' && !isEmpty && (
            <div style={{ padding: '40px 0' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {[1, 2, 3].map(i => (
                  <div
                    key={i}
                    style={{
                      height: '14px',
                      background: 'linear-gradient(90deg, #e2e8f0 25%, #f1f5f9 50%, #e2e8f0 75%)',
                      backgroundSize: '200% 100%',
                      borderRadius: '4px',
                      animation: 'shimmer 1.5s ease-in-out infinite',
                      width: `${60 + i * 15}%`,
                    }}
                  />
                ))}
              </div>
              <style>{`
                @keyframes shimmer {
                  0% { background-position: 200% 0; }
                  100% { background-position: -200% 0; }
                }
              `}</style>
              <p style={{ fontSize: '11px', color: '#94a3b8', marginTop: '16px', textAlign: 'center' }}>
                Generating report from audit log...
              </p>
            </div>
          )}

          {/* ERROR STATE */}
          {state === 'error' && !isEmpty && (
            <div style={{ textAlign: 'center', padding: '40px 20px' }}>
              <div style={{ fontSize: '36px', marginBottom: '8px' }}>⚠️</div>
              <p style={{ fontSize: '13px', fontWeight: 600, color: '#ef4444' }}>
                Report generation failed
              </p>
              <p style={{ fontSize: '11px', color: '#94a3b8', marginTop: '6px', maxWidth: '400px', marginLeft: 'auto', marginRight: 'auto' }}>
                Check console for details.
              </p>
              <button
                onClick={loadReport}
                style={{
                  marginTop: '12px',
                  padding: '8px 20px',
                  fontSize: '12px',
                  fontWeight: 600,
                  background: '#6366f1',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                }}
              >
                Retry
              </button>
            </div>
          )}

          {/* SUCCESS STATE — render report */}
          {state === 'success' && report && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              {/* Generated timestamp */}
              <div style={{ fontSize: '11px', color: '#64748b' }}>
                Generated {new Date(report.generatedAt).toLocaleDateString('en-IN', {
                  year: 'numeric', month: 'long', day: 'numeric',
                  hour: '2-digit', minute: '2-digit',
                })}
              </div>
              <hr style={{ border: 'none', borderTop: '1px solid #e2e8f0', margin: 0 }} />

              {/* SECTION 1 — Executive Summary */}
              <div>
                <SectionLabel>Executive Summary</SectionLabel>
                <p style={{ fontSize: '13px', lineHeight: 1.6, color: '#334155' }}>{report.summary}</p>
              </div>

              {/* SECTION 2 — Metrics 4-up grid */}
              <div>
                <SectionLabel>Metrics</SectionLabel>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px' }}>
                  <StatCard value={report.escalationsRaised} label="Raised" color="#3b82f6" />
                  <StatCard value={report.escalationsApproved} label="Approved" color="#22c55e" />
                  <StatCard value={report.escalationsRejected} label="Rejected" color="#ef4444" />
                  <StatCard value={report.overridesIssued} label="Overrides" color="#f59e0b" />
                </div>
              </div>

              {/* SECTION 3 — Agents Deployed */}
              <div>
                <SectionLabel>Agents Deployed</SectionLabel>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                  {report.agentsDeployed.map(a => (
                    <span
                      key={a}
                      style={{
                        background: '#f1f5f9',
                        borderRadius: '999px',
                        padding: '3px 12px',
                        fontSize: '12px',
                        fontFamily: 'monospace',
                        color: '#1e293b',
                      }}
                    >
                      {a}
                    </span>
                  ))}
                </div>
              </div>

              {/* SECTION 4 — Decision Timeline */}
              <div>
                <SectionLabel>Decision Timeline</SectionLabel>
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  {report.timeline.map((t, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        gap: '12px',
                        padding: '8px 0',
                        borderBottom: i < report.timeline.length - 1 ? '1px solid #f1f5f9' : 'none',
                        background: i % 2 === 0 ? 'transparent' : '#f8fafc',
                      }}
                    >
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: '44px' }}>
                        <span style={{ color: '#3b82f6', fontSize: '8px' }}>●</span>
                        {i < report.timeline.length - 1 && (
                          <div style={{ width: '1px', flex: 1, background: '#e2e8f0', marginTop: '2px' }} />
                        )}
                      </div>
                      <div>
                        <span style={{ fontWeight: 700, fontSize: '10px', color: '#475569' }}>{t.time}</span>
                        <div style={{ fontWeight: 600, fontSize: '12px', color: '#1e293b', marginTop: '1px' }}>{t.event}</div>
                        <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px' }}>
                          {t.actor} · {t.outcome}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* SECTION 5 — Critical Decisions */}
              <div>
                <SectionLabel>Critical Decisions</SectionLabel>
                {report.criticalDecisions.map((d, i) => (
                  <div
                    key={i}
                    style={{
                      background: '#f8fafc',
                      border: '1px solid #e2e8f0',
                      borderRadius: '8px',
                      padding: '12px',
                      marginBottom: '8px',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: '22px',
                          height: '22px',
                          borderRadius: '50%',
                          background: '#1e293b',
                          color: '#fff',
                          fontSize: '10px',
                          fontWeight: 700,
                          flexShrink: 0,
                        }}
                      >
                        {i + 1}
                      </span>
                      <span style={{ fontWeight: 700, fontSize: '13px' }}>{d.decision}</span>
                    </div>
                    <p style={{ fontSize: '11px', color: '#475569', margin: '4px 0' }}>{d.rationale}</p>
                    <p style={{ fontSize: '11px', color: '#64748b', fontStyle: 'italic', margin: 0 }}>{d.impact}</p>
                  </div>
                ))}
              </div>

              {/* SECTION 6 — Lessons Learned */}
              <div>
                <SectionLabel>Lessons Learned</SectionLabel>
                {report.lessonsLearned.map((l, i) => (
                  <div
                    key={i}
                    style={{
                      borderLeft: '4px solid #f59e0b',
                      paddingLeft: '12px',
                      marginBottom: '8px',
                      fontSize: '12px',
                      color: '#334155',
                      lineHeight: 1.5,
                    }}
                  >
                    {l}
                  </div>
                ))}
              </div>

              {/* SECTION 7 — Recommendations */}
              <div>
                <SectionLabel>Recommendations</SectionLabel>
                {report.recommendations.map((r, i) => (
                  <div
                    key={i}
                    style={{
                      borderLeft: '4px solid #3b82f6',
                      paddingLeft: '12px',
                      marginBottom: '8px',
                      fontSize: '12px',
                      color: '#334155',
                      lineHeight: 1.5,
                    }}
                  >
                    <span style={{ fontWeight: 700 }}>{i + 1}.</span> {r}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer actions */}
        {!isEmpty && state === 'success' && (
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: '10px',
              padding: '12px 20px',
              borderTop: '1px solid #e2e8f0',
              flexShrink: 0,
            }}
          >
            <button
              onClick={onClose}
              style={{
                padding: '8px 18px',
                fontSize: '12px',
                fontWeight: 600,
                background: '#f1f5f9',
                color: '#475569',
                border: '1px solid #e2e8f0',
                borderRadius: '6px',
                cursor: 'pointer',
              }}
            >
              Close
            </button>
            <button
              onClick={handleExportPdf}
              disabled={exporting}
              style={{
                padding: '8px 18px',
                fontSize: '12px',
                fontWeight: 600,
                background: exporting ? '#c7d2fe' : '#6366f1',
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                cursor: exporting ? 'not-allowed' : 'pointer',
              }}
            >
              {exporting ? 'Opening print dialog...' : 'Download PDF'}
            </button>
          </div>
        )}

        {/* Footer for empty/error — just close */}
        {(isEmpty || state === 'error') && (
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              padding: '12px 20px',
              borderTop: '1px solid #e2e8f0',
              flexShrink: 0,
            }}
          >
            <button
              onClick={onClose}
              style={{
                padding: '8px 18px',
                fontSize: '12px',
                fontWeight: 600,
                background: '#f1f5f9',
                color: '#475569',
                border: '1px solid #e2e8f0',
                borderRadius: '6px',
                cursor: 'pointer',
              }}
            >
              Close
            </button>
          </div>
        )}
      </div>
    </>
  )
}

function SectionLabel({ children }: { children: string }) {
  return (
    <h3
      style={{
        fontSize: '10px',
        fontWeight: 700,
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        color: '#64748b',
        marginBottom: '8px',
        marginTop: 0,
      }}
    >
      {children}
    </h3>
  )
}

function StatCard({ value, label, color }: { value: number; label: string; color: string }) {
  return (
    <div
      style={{
        background: '#f8fafc',
        border: '1px solid #e2e8f0',
        borderRadius: '8px',
        padding: '10px 8px',
        textAlign: 'center',
      }}
    >
      <div style={{ fontSize: '26px', fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.05em', color: '#94a3b8', marginTop: '2px', textTransform: 'uppercase' }}>
        {label}
      </div>
    </div>
  )
}
