import { useState, useEffect } from 'react'

interface BackendStatusBadgeProps {
  connectionState: 'connecting' | 'live' | 'reconnecting' | 'offline'
  lastMessageTime?: number  // Date.now() of last WS message received
  retryCount?: number       // current retry attempt (for reconnecting display)
  maxRetries?: number       // default 5
}

export function BackendStatusBadge({
  connectionState,
  lastMessageTime,
  retryCount = 0,
  maxRetries = 5,
}: BackendStatusBadgeProps) {
  const [stale, setStale] = useState(false)

  // Check for staleness — if live but no message for 60s
  useEffect(() => {
    if (connectionState !== 'live' || !lastMessageTime) {
      setStale(false)
      return
    }

    const check = () => {
      setStale(Date.now() - lastMessageTime > 60_000)
    }

    check()
    const interval = setInterval(check, 10_000)
    return () => clearInterval(interval)
  }, [connectionState, lastMessageTime])

  // States
  if (connectionState === 'live' && !stale) {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 8px',
          fontSize: '9px',
          fontWeight: 700,
          letterSpacing: '0.08em',
          background: 'rgba(0, 230, 118, 0.1)',
          border: '1px solid rgba(0, 230, 118, 0.35)',
          color: '#00e676',
          borderRadius: '3px',
          animation: 'pulse-green-dot-anim 2s ease-in-out infinite',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{
          width: '5px',
          height: '5px',
          borderRadius: '50%',
          background: '#00e676',
          display: 'inline-block',
        }} />
        LIVE
      </span>
    )
  }

  if (connectionState === 'live' && stale) {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 8px',
          fontSize: '9px',
          fontWeight: 700,
          letterSpacing: '0.08em',
          background: 'rgba(249, 115, 22, 0.1)',
          border: '1px solid rgba(249, 115, 22, 0.35)',
          color: '#f97316',
          borderRadius: '3px',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{
          width: '5px',
          height: '5px',
          borderRadius: '50%',
          background: '#f97316',
          display: 'inline-block',
        }} />
        STALE
      </span>
    )
  }

  if (connectionState === 'connecting') {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 8px',
          fontSize: '9px',
          fontWeight: 700,
          letterSpacing: '0.08em',
          background: 'rgba(250, 204, 21, 0.1)',
          border: '1px solid rgba(250, 204, 21, 0.35)',
          color: '#eab308',
          borderRadius: '3px',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{
          width: '5px',
          height: '5px',
          borderRadius: '50%',
          background: '#eab308',
          display: 'inline-block',
          animation: 'pulse-green-dot-anim 0.6s ease-in-out infinite',
        }} />
        CONNECTING...
      </span>
    )
  }

  if (connectionState === 'reconnecting') {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 8px',
          fontSize: '9px',
          fontWeight: 700,
          letterSpacing: '0.08em',
          background: 'rgba(250, 204, 21, 0.1)',
          border: '1px solid rgba(250, 204, 21, 0.35)',
          color: '#eab308',
          borderRadius: '3px',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{
          width: '5px',
          height: '5px',
          borderRadius: '50%',
          background: '#eab308',
          display: 'inline-block',
          animation: 'pulse-green-dot-anim 0.6s ease-in-out infinite',
        }} />
        RECONNECTING ({retryCount}/{maxRetries})
      </span>
    )
  }

  // offline
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '2px 8px',
        fontSize: '9px',
        fontWeight: 700,
        letterSpacing: '0.08em',
        background: 'rgba(239, 68, 68, 0.08)',
        border: '1px solid rgba(239, 68, 68, 0.25)',
        color: '#ef4444',
        borderRadius: '3px',
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{
        width: '5px',
        height: '5px',
        borderRadius: '50%',
        background: '#ef4444',
        display: 'inline-block',
      }} />
      OFFLINE — MOCK DATA
    </span>
  )
}
