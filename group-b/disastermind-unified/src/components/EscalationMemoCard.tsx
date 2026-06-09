import { useState, useEffect } from 'react'
import type { EscalationItem } from '../lib/mapTypes'

interface Props {
  item: EscalationItem
  onApprove: (id: string) => void
  onOverride: (id: string, reason: string) => void
}

function formatCountdown(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(totalSeconds / 60).toString().padStart(2, '0')
  const s = (totalSeconds % 60).toString().padStart(2, '0')
  return `${m}:${s}`
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

const PRIORITY_COLORS: Record<string, string> = {
  CRITICAL: '#ef4444',
  HIGH: '#f97316',
  MEDIUM: '#3b82f6',
}

const TRIGGER_LABELS: Record<string, string> = {
  CROSS_STATE_RESOURCE: 'CROSS-STATE RESOURCE',
  MILITARY_ASSET: 'MILITARY ASSET',
  MANDATORY_EVACUATION: 'MANDATORY EVACUATION',
  REQUISITION_INFRASTRUCTURE: 'REQUISITION INFRASTRUCTURE',
  MEDIA_BROADCAST: 'MEDIA BROADCAST',
  INTERNATIONAL_AID: 'INTERNATIONAL AID',
  STATE_OF_EMERGENCY: 'STATE OF EMERGENCY',
  ARMED_FORCES: 'ARMED FORCES',
  CRITICAL_INFRASTRUCTURE: 'CRITICAL INFRASTRUCTURE',
}

export function EscalationMemoCard({ item, onApprove, onOverride }: Props) {
  const [showOverride, setShowOverride] = useState(false)
  const [overrideReason, setOverrideReason] = useState('')
  const [now, setNow] = useState(Date.now())

  // Live countdown ticker
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const elapsed = now - item.createdAt
  const remaining = Math.max(0, item.timeoutMs - elapsed)
  const isHumanOnly = item.timeoutMs === Infinity
  const isResolved = item.status !== 'PENDING'

  // Countdown color
  let countdownColor = '#22c55e' // green > 3min
  if (remaining <= 60000) countdownColor = '#ef4444' // red < 1min
  else if (remaining <= 180000) countdownColor = '#f97316' // amber 1-3min

  const priorityColor = PRIORITY_COLORS[item.priority] ?? '#64748b'
  const triggerLabel = TRIGGER_LABELS[item.trigger] ?? item.trigger

  const handleApprove = () => {
    onApprove(item.id)
  }

  const handleOverrideConfirm = () => {
    if (overrideReason.trim().length >= 20) {
      onOverride(item.id, overrideReason.trim())
      setShowOverride(false)
      setOverrideReason('')
    }
  }

  const handleCancelOverride = () => {
    setShowOverride(false)
    setOverrideReason('')
  }

  // Status display for resolved items
  if (isResolved) {
    const statusColor = item.status === 'APPROVED' ? '#22c55e'
      : item.status === 'OVERRIDDEN' ? '#ef4444'
      : '#64748b'
    const statusIcon = item.status === 'APPROVED' ? '✓'
      : item.status === 'OVERRIDDEN' ? '✗'
      : '⚡'
    const statusLabel = item.status === 'APPROVED' ? 'APPROVED'
      : item.status === 'OVERRIDDEN' ? 'OVERRIDDEN'
      : 'AUTO-EXECUTED'

    return (
      <article style={{
        border: `1px solid ${statusColor}44`,
        background: `${statusColor}0d`,
        borderRadius: '4px',
        padding: '10px',
        opacity: 0.6,
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '8px',
        }}>
          <span style={{
            fontSize: '11px',
            fontWeight: 700,
            fontFamily: 'var(--font-mono)',
            color: statusColor,
          }}>
            {statusIcon} {statusLabel}
          </span>
          <span style={{ fontSize: '10px', color: '#64748b', fontFamily: 'var(--font-mono)' }}>
            {item.resolvedAt ? formatTime(item.resolvedAt) : ''}
          </span>
        </div>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          marginTop: '4px',
          fontSize: '10px',
          color: '#94a3b8',
        }}>
          <span>{item.id}</span>
          {item.status === 'OVERRIDDEN' && item.overrideReason && (
            <span style={{ color: '#f97316' }}>— "{item.overrideReason}"</span>
          )}
        </div>
      </article>
    )
  }

  return (
    <article style={{
      background: '#1a1f2e',
      border: '1px solid rgba(58, 74, 107, 0.76)',
      borderRadius: '4px',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '8px 10px',
        background: 'rgba(10, 13, 20, 0.42)',
        borderBottom: '1px solid rgba(58, 74, 107, 0.64)',
      }}>
        {/* Priority badge */}
        <span style={{
          padding: '2px 6px',
          fontSize: '9px',
          fontWeight: 700,
          letterSpacing: '0.08em',
          color: '#fff',
          background: priorityColor,
          borderRadius: '3px',
        }}>
          {item.priority}
        </span>
        {/* Trigger label */}
        <span style={{
          fontSize: '11px',
          fontWeight: 700,
          fontFamily: 'var(--font-heading)',
          letterSpacing: '0.05em',
          color: '#e2e8f0',
          flex: 1,
        }}>
          {triggerLabel}
        </span>
        {/* Zone */}
        <span style={{
          fontSize: '9px',
          color: '#64748b',
          whiteSpace: 'nowrap',
        }}>
          {item.zone}
        </span>
      </div>

      {/* Countdown */}
      <div style={{
        padding: '4px 10px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        borderBottom: '1px solid rgba(58, 74, 107, 0.4)',
      }}>
        {isHumanOnly ? (
          <span style={{
            fontSize: '10px',
            fontWeight: 600,
            color: '#f97316',
          }}>
            ⚠ HUMAN DECISION REQUIRED — No auto-execute
          </span>
        ) : (
          <>
            <span style={{
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.08em',
              color: '#64748b',
            }}>
              DECISION WINDOW
            </span>
            <span style={{
              fontSize: '14px',
              fontWeight: 700,
              fontFamily: 'var(--font-mono)',
              color: countdownColor,
              transition: 'color 0.3s ease',
            }}>
              {formatCountdown(remaining)}
            </span>
          </>
        )}
      </div>

      {/* Memo body */}
      <div style={{ padding: '10px' }}>
        {[
          { label: 'SITUATION', value: item.memo.situation },
          { label: 'RECOMMENDED', value: item.memo.recommended },
          { label: 'RISK IF YES', value: item.memo.riskIfYes },
          { label: 'RISK IF NO', value: item.memo.riskIfNo },
        ].map((row, i) => (
          <div key={i} style={{
            display: 'flex',
            gap: '10px',
            marginBottom: i < 3 ? '8px' : '0',
            paddingLeft: '8px',
            borderLeft: `2px solid ${priorityColor}44`,
          }}>
            <span style={{
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.08em',
              color: '#64748b',
              minWidth: '80px',
              flexShrink: 0,
              paddingTop: '1px',
            }}>
              {row.label}
            </span>
            <span style={{
              fontSize: '11px',
              lineHeight: '1.45',
              color: '#cbd5e1',
            }}>
              {row.value}
            </span>
          </div>
        ))}
      </div>

      {/* Actions */}
      <div style={{
        padding: '8px 10px',
        borderTop: '1px solid rgba(58, 74, 107, 0.4)',
      }}>
        {showOverride ? (
          /* Override form */
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
          }}>
            <label style={{
              fontSize: '10px',
              fontWeight: 600,
              color: '#f97316',
            }}>
              Enter override reason (required)
            </label>
            <textarea
              value={overrideReason}
              onChange={e => setOverrideReason(e.target.value)}
              placeholder="Describe why this action is being overridden..."
              style={{
                width: '100%',
                minHeight: '60px',
                padding: '8px',
                fontSize: '11px',
                fontFamily: 'var(--font-mono)',
                color: '#e2e8f0',
                background: 'rgba(10, 13, 20, 0.6)',
                border: '1px solid rgba(58, 74, 107, 0.6)',
                borderRadius: '3px',
                resize: 'vertical',
                outline: 'none',
              }}
            />
            <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
              <button
                type="button"
                onClick={handleCancelOverride}
                style={{
                  padding: '5px 10px',
                  fontSize: '10px',
                  fontWeight: 700,
                  letterSpacing: '0.05em',
                  background: 'transparent',
                  border: '1px solid rgba(255,255,255,0.15)',
                  color: '#94a3b8',
                  borderRadius: '3px',
                  cursor: 'pointer',
                }}
              >
                CANCEL
              </button>
              <button
                type="button"
                onClick={handleOverrideConfirm}
                disabled={overrideReason.trim().length < 20}
                style={{
                  padding: '5px 10px',
                  fontSize: '10px',
                  fontWeight: 700,
                  letterSpacing: '0.05em',
                  background: overrideReason.trim().length >= 20 ? '#ef4444' : 'rgba(239,68,68,0.3)',
                  border: '1px solid #ef4444',
                  color: '#fff',
                  borderRadius: '3px',
                  cursor: overrideReason.trim().length >= 20 ? 'pointer' : 'not-allowed',
                }}
              >
                CONFIRM OVERRIDE
              </button>
            </div>
          </div>
        ) : (
          /* Approve / Override buttons */
          <div style={{ display: 'flex', gap: '6px' }}>
            <button
              type="button"
              onClick={handleApprove}
              style={{
                flex: 1,
                padding: '6px 0',
                fontSize: '11px',
                fontWeight: 700,
                letterSpacing: '0.08em',
                background: 'transparent',
                border: '1px solid rgba(0, 212, 255, 0.5)',
                color: '#00d4ff',
                borderRadius: '3px',
                cursor: 'pointer',
                transition: 'background 0.18s ease',
              }}
            >
              APPROVE
            </button>
            <button
              type="button"
              onClick={() => setShowOverride(true)}
              style={{
                flex: 1,
                padding: '6px 0',
                fontSize: '11px',
                fontWeight: 700,
                letterSpacing: '0.08em',
                background: 'transparent',
                border: '1px solid rgba(255, 59, 59, 0.5)',
                color: '#ef4444',
                borderRadius: '3px',
                cursor: 'pointer',
                transition: 'background 0.18s ease',
              }}
            >
              OVERRIDE
            </button>
          </div>
        )}
      </div>
    </article>
  )
}
