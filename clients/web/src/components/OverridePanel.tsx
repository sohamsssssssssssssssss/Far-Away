import { useState, useEffect } from 'react'
import type { ShapFeature } from '../lib/mapTypes'

interface DecisionData {
  id: string
  agentType: string
  action: string
  reasoning: string
  shapFeatures?: ShapFeature[]
  confidence?: number
}

interface OverridePanelProps {
  decision: DecisionData | null
  onClose: () => void
  onConfirm: (reason: string) => void
}

const AGENT_COLORS: Record<string, string> = {
  'FLOOD-AI': '#00bcd4',
  'RESOURCE-AI': '#4caf50',
  'EVAC-AI': '#2196f3',
  'COORD-AI': '#ff9800',
}

export function OverridePanel({ decision, onClose, onConfirm }: OverridePanelProps) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [touched, setTouched] = useState(false)

  const isValid = reason.trim().length >= 20

  // Reset state when opening a new decision
  useEffect(() => {
    if (decision) {
      setReason('')
      setSubmitting(false)
      setTouched(false)
    }
  }, [decision?.id])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && decision) onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [decision, onClose])

  if (!decision) return null

  const agentColor = AGENT_COLORS[decision.agentType] ?? '#94a3b8'
  const confPct = decision.confidence !== undefined ? Math.round(decision.confidence * 100) : null
  const now = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })

  const handleConfirm = () => {
    if (!isValid) return
    setSubmitting(true)
    setTimeout(() => {
      onConfirm(reason)
      setSubmitting(false)
      onClose()
    }, 800)
  }

  return (
    <>
      {/* Backdrop */}
      <div
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.5)',
          zIndex: 999,
          animation: 'fadeIn 0.15s ease',
        }}
        onClick={onClose}
      />
      {/* Panel */}
      <div
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          width: '420px',
          height: '100vh',
          background: '#0f1420',
          borderLeft: '1px solid rgba(255,255,255,0.08)',
          zIndex: 1000,
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '-4px 0 24px rgba(0,0,0,0.4)',
          animation: 'slideInRight 0.25s ease',
          fontFamily: 'var(--font-mono, monospace)',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '14px 16px',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
        }}>
          <div>
            <div style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.08em', color: '#ef4444' }}>
              ⚠ OVERRIDE AGENT DECISION
            </div>
            <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px' }}>
              Commander CDR-SOHAM  •  {now}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: '#64748b',
              fontSize: '16px',
              cursor: 'pointer',
              padding: '4px',
              lineHeight: 1,
            }}
          >
            ✕
          </button>
        </div>

        {/* Scrollable body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>

          {/* SECTION 1 — Original Decision */}
          <div>
            <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.1em', color: '#475569', marginBottom: '6px' }}>
              ORIGINAL DECISION
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
              <span style={{
                display: 'inline-block',
                padding: '1px 6px',
                fontSize: '9px',
                fontWeight: 700,
                borderRadius: '2px',
                background: `${agentColor}20`,
                color: agentColor,
                border: `1px solid ${agentColor}40`,
              }}>
                {decision.agentType}
              </span>
              {confPct !== null && (
                <span style={{
                  fontSize: '9px',
                  fontWeight: 700,
                  color: confPct >= 90 ? '#22c55e' : confPct >= 75 ? '#f59e0b' : '#ef4444',
                }}>
                  {confPct}% CONF
                </span>
              )}
            </div>
            <div style={{ fontSize: '12px', color: '#e2e8f0', lineHeight: 1.5 }}>
              {decision.action}
            </div>
          </div>

          {/* SECTION 2 — Reasoning Chain */}
          <div>
            <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.1em', color: '#475569', marginBottom: '6px' }}>
              AGENT REASONING
            </div>
            <div style={{
              fontSize: '11px',
              color: '#94a3b8',
              lineHeight: 1.7,
              fontFamily: 'var(--font-mono, monospace)',
              background: 'rgba(0,0,0,0.2)',
              padding: '8px 10px',
              borderRadius: '4px',
              border: '1px solid rgba(255,255,255,0.04)',
            }}>
              {decision.reasoning}
            </div>
          </div>

          {/* SECTION 3 — Model Drivers (SHAP) */}
          {decision.shapFeatures && decision.shapFeatures.length > 0 && (
            <div>
              <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.1em', color: '#475569', marginBottom: '6px' }}>
                MODEL DRIVERS
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                {decision.shapFeatures.map((f, i) => {
                  const isUp = f.direction === 'up'
                  const color = isUp ? '#ef4444' : '#22c55e'
                  const bg = isUp ? 'rgba(239,68,68,0.1)' : 'rgba(34,197,94,0.1)'
                  const border = isUp ? 'rgba(239,68,68,0.25)' : 'rgba(34,197,94,0.25)'
                  return (
                    <span
                      key={i}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '3px',
                        padding: '2px 7px',
                        fontSize: '10px',
                        fontWeight: 600,
                        color,
                        background: bg,
                        border: `1px solid ${border}`,
                        borderRadius: '3px',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      <span style={{ fontSize: '9px' }}>{isUp ? '↑' : '↓'}</span>
                      {f.label}
                    </span>
                  )
                })}
              </div>
            </div>
          )}

          {/* SECTION 4 — Override Reason */}
          <div>
            <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.1em', color: '#475569', marginBottom: '6px' }}>
              OVERRIDE REASON  *
            </div>
            <textarea
              value={reason}
              onChange={(e) => { setReason(e.target.value); setTouched(true) }}
              placeholder="Describe why this decision is being overridden. This is permanently logged and audited."
              rows={4}
              style={{
                width: '100%',
                padding: '8px 10px',
                fontSize: '11px',
                fontFamily: 'var(--font-mono, monospace)',
                background: 'rgba(0,0,0,0.25)',
                color: '#e2e8f0',
                border: `1px solid ${
                  touched && !isValid ? '#ef4444'
                  : touched && isValid ? '#22c55e'
                  : 'rgba(255,255,255,0.1)'
                }`,
                borderRadius: '4px',
                resize: 'vertical',
                minHeight: '72px',
                outline: 'none',
                lineHeight: 1.5,
              }}
            />
            <div style={{
              fontSize: '9px',
              color: reason.length >= 20 ? '#22c55e' : '#64748b',
              marginTop: '4px',
              textAlign: 'right',
              fontWeight: 600,
            }}>
              {reason.length} / 20 min
            </div>
          </div>

          {/* SECTION 5 — Propagation Notice */}
          <div style={{
            background: 'rgba(59,130,246,0.06)',
            border: '1px solid rgba(59,130,246,0.15)',
            borderRadius: '4px',
            padding: '8px 10px',
          }}>
            <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.08em', color: '#60a5fa', marginBottom: '4px' }}>
              This override will propagate to:
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px', marginBottom: '4px' }}>
              {(decision.id ? true : false) && (() => {
                // Determine propagated agents from a mini propagation map
                const pmap: Record<string, string[]> = {
                  'FLOOD-AI': ['RESOURCE-AI', 'ROUTING-AI', 'FIELD-COORD-AI'],
                  'RESOURCE-AI': ['ROUTING-AI', 'FIELD-COORD-AI'],
                  'ROUTING-AI': ['FIELD-COORD-AI'],
                  'COMMANDER-AI': ['FLOOD-AI', 'RESOURCE-AI', 'ROUTING-AI', 'FIELD-COORD-AI'],
                  'SHELTER-AI': ['RESOURCE-AI', 'FIELD-COORD-AI'],
                }
                const agents = pmap[decision.agentType] ?? ['COMMANDER-AI']
                return agents.map((a: string) => (
                  <span
                    key={a}
                    style={{
                      display: 'inline-block',
                      padding: '1px 5px',
                      fontSize: '9px',
                      fontWeight: 600,
                      background: 'rgba(59,130,246,0.12)',
                      color: '#60a5fa',
                      borderRadius: '3px',
                      border: '1px solid rgba(59,130,246,0.2)',
                    }}
                  >
                    {a}
                  </span>
                ))
              })()}
            </div>
            <div style={{ fontSize: '9px', color: '#64748b', fontStyle: 'italic' }}>
              All listed agents will receive the updated instruction.
            </div>
          </div>
        </div>

        {/* Footer buttons */}
        <div style={{
          display: 'flex',
          gap: '8px',
          padding: '12px 16px',
          borderTop: '1px solid rgba(255,255,255,0.06)',
        }}>
          <button
            onClick={onClose}
            style={{
              flex: 1,
              padding: '8px 12px',
              fontSize: '10px',
              fontWeight: 700,
              letterSpacing: '0.08em',
              background: 'transparent',
              border: '1px solid rgba(255,255,255,0.12)',
              color: '#94a3b8',
              borderRadius: '4px',
              cursor: 'pointer',
            }}
          >
            CANCEL
          </button>
          <button
            onClick={handleConfirm}
            disabled={!isValid || submitting}
            style={{
              flex: 1,
              padding: '8px 12px',
              fontSize: '10px',
              fontWeight: 700,
              letterSpacing: '0.08em',
              background: isValid && !submitting ? 'rgba(239,68,68,0.15)' : 'transparent',
              border: `1px solid ${
                isValid && !submitting ? 'rgba(239,68,68,0.4)' : 'rgba(255,255,255,0.08)'
              }`,
              color: isValid && !submitting ? '#ef4444' : '#475569',
              borderRadius: '4px',
              cursor: isValid && !submitting ? 'pointer' : 'not-allowed',
              transition: 'all 0.2s ease',
            }}
          >
            {submitting ? 'SUBMITTING...' : 'CONFIRM OVERRIDE'}
          </button>
        </div>
      </div>

      {/* Keyframe styles injected inline */}
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
    </>
  )
}
