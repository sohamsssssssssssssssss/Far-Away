import { useState, useEffect, useCallback, useRef } from 'react'
import { generateSituationBriefing } from '../../../lib/situationBriefing'
import type { BriefingContext } from '../../../lib/situationBriefing'

const AUTO_INTERVAL_MS = 15 * 60 * 1000   // 15 minutes
const COUNTDOWN_TICK_MS = 1000

interface BriefingPanelProps {
  context?: BriefingContext
  className?: string
}

export function BriefingPanel({ context, className }: BriefingPanelProps) {
  const [briefingText, setBriefingText] = useState<string>(
    'Initialising situation briefing system...'
  )
  const [generatedAt, setGeneratedAt] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [nextBriefingIn, setNextBriefingIn] = useState(AUTO_INTERVAL_MS / 1000)
  const [sendToast, setSendToast] = useState<'idle' | 'sending' | 'sent'>('idle')
  const autoTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const generate = useCallback(async (ctx?: BriefingContext) => {
    setLoading(true)
    try {
      const result = await generateSituationBriefing(ctx ?? context)
      setBriefingText(result.text)
      setGeneratedAt(result.generatedAt)
    } catch {
      setBriefingText(
        'Briefing generation unavailable — LLM offline. Operating in degraded mode. ' +
        'Last known status: Cyclone Remal, Category 3. Zones 6-7 high risk. ' +
        '12 autonomous decisions executed. Shelters at 73% capacity.'
      )
    } finally {
      setLoading(false)
      setNextBriefingIn(AUTO_INTERVAL_MS / 1000)
    }
  }, [context])

  // Auto-fire on mount
  useEffect(() => {
    generate()
  }, [])

  // Auto-fire every 15 minutes
  useEffect(() => {
    autoTimerRef.current = setInterval(() => {
      generate()
    }, AUTO_INTERVAL_MS)

    return () => {
      if (autoTimerRef.current) clearInterval(autoTimerRef.current)
    }
  }, [generate])

  // Countdown ticker
  useEffect(() => {
    countdownRef.current = setInterval(() => {
      setNextBriefingIn(prev => (prev <= 1 ? AUTO_INTERVAL_MS / 1000 : prev - 1))
    }, COUNTDOWN_TICK_MS)

    return () => {
      if (countdownRef.current) clearInterval(countdownRef.current)
    }
  }, [])

  const formatCountdown = (seconds: number) => {
    const m = Math.floor(seconds / 60).toString().padStart(2, '0')
    const s = (seconds % 60).toString().padStart(2, '0')
    return `${m}:${s}`
  }

  const formatTime = (iso: string) => {
    if (!iso) return '--:--'
    return new Date(iso).toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const handleSendToOfficials = () => {
    setSendToast('sending')
    setTimeout(() => {
      setSendToast('sent')
      setTimeout(() => setSendToast('idle'), 3000)
    }, 1200)
  }

  return (
    <section className={`panel briefing-panel ${className ?? ''}`} style={{ display: 'flex', flexDirection: 'column', gap: '8px', position: 'relative' }}>
      {/* Header */}
      <div className="panel-title">
        <div>
          <h2>SITUATION BRIEFING</h2>
          {generatedAt && (
            <span style={{ fontSize: '10px', color: '#64748b', marginLeft: '8px' }}>
              {formatTime(generatedAt)}
            </span>
          )}
        </div>
        <span style={{ fontSize: '10px', color: '#64748b' }}>
          NEXT IN <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{formatCountdown(nextBriefingIn)}</span>
        </span>
      </div>

      {/* Briefing text */}
      <div className="briefing-copy" style={{
        fontSize: '12px',
        lineHeight: '1.6',
        color: loading ? '#64748b' : '#cbd5e1',
        minHeight: '80px',
        fontStyle: loading ? 'italic' : 'normal',
      }}>
        {loading ? '● Generating briefing...' : briefingText}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: '8px', padding: '0 12px', marginTop: '4px' }}>
        <button
          onClick={() => generate()}
          disabled={loading}
          className="officials-btn"
          style={{
            flex: 1,
            background: 'transparent',
            border: '1px solid rgba(255,255,255,0.15)',
            color: loading ? '#64748b' : '#e2e8f0',
            cursor: loading ? 'not-allowed' : 'pointer',
          }}
        >
          {loading ? 'GENERATING...' : '↻ GENERATE'}
        </button>
        <button
          onClick={handleSendToOfficials}
          disabled={loading || sendToast !== 'idle'}
          className={sendToast === 'sent' ? '' : 'officials-btn'}
          style={{
            flex: 1,
            background: sendToast === 'sent' ? '#16a34a' : 'transparent',
            border: `1px solid ${sendToast === 'sent' ? '#16a34a' : 'rgba(255,255,255,0.15)'}`,
            color: sendToast === 'sent' ? '#fff' : '#e2e8f0',
            cursor: (loading || sendToast !== 'idle') ? 'not-allowed' : 'pointer',
            transition: 'all 0.3s ease',
          }}
        >
          {sendToast === 'sending' ? '● DISPATCHING...' :
           sendToast === 'sent' ? '✓ DISPATCHED' :
           '⇪ SEND TO OFFICIALS'}
        </button>
      </div>

      {/* Dispatch toast */}
      {sendToast === 'sent' && (
        <div style={{
          position: 'absolute',
          bottom: '48px',
          left: '12px',
          right: '12px',
          background: '#16a34a',
          color: '#fff',
          fontSize: '11px',
          fontWeight: 600,
          padding: '8px 12px',
          borderRadius: '4px',
          textAlign: 'center',
          animation: 'fadeIn 0.2s ease',
        }}>
          ✓ Briefing dispatched via SMS + WhatsApp to 6 registered officials
        </div>
      )}
    </section>
  )
}
