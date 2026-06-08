import { useEffect, useMemo, useState } from 'react'

type CountdownTimerProps = {
  durationSeconds?: number
}

const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}

export function CountdownTimer({ durationSeconds = 300 }: CountdownTimerProps) {
  const [remaining, setRemaining] = useState(durationSeconds)

  useEffect(() => {
    const interval = window.setInterval(() => {
      setRemaining((current) => Math.max(0, current - 1))
    }, 1000)

    return () => window.clearInterval(interval)
  }, [durationSeconds])

  const tone = useMemo(() => {
    if (remaining === 0) return 'expired'
    if (remaining < 60) return 'danger'
    if (remaining <= 180) return 'warning'
    return 'success'
  }, [remaining])

  return (
    <div className={`countdown ${tone}`} aria-live="polite">
      <span>COMMAND AUTHORITY WINDOW</span>
      <strong>{remaining === 0 ? 'AUTO-EXECUTING...' : formatTime(remaining)}</strong>
    </div>
  )
}
