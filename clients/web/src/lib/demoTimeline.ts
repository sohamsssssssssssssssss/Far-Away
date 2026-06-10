import { useState, useEffect, useRef } from 'react'

export interface DemoTimelineCallbacks {
  onRiverWarning: () => void
  onZone7Escalation: () => void
  onAutoExecute: () => void
  onAllClear: () => void
}

export function useDemoTimeline({
  onRiverWarning,
  onZone7Escalation,
  onAutoExecute,
  onAllClear,
}: DemoTimelineCallbacks) {
  const [demoStatus, setDemoStatus] = useState<'idle' | 'running' | 'completed'>('idle')
  const [escalationApproved, setEscalationApproved] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  const callbacksRef = useRef({ onRiverWarning, onZone7Escalation, onAutoExecute, onAllClear })
  useEffect(() => {
    callbacksRef.current = { onRiverWarning, onZone7Escalation, onAutoExecute, onAllClear }
  }, [onRiverWarning, onZone7Escalation, onAutoExecute, onAllClear])

  const startDemo = () => {
    setDemoStatus('running')
    setEscalationApproved(false)
    setElapsedSeconds(0)
  }

  const approvedRef = useRef(escalationApproved)
  useEffect(() => {
    approvedRef.current = escalationApproved
  }, [escalationApproved])

  useEffect(() => {
    if (demoStatus !== 'running') return

    const timer = setInterval(() => {
      setElapsedSeconds((prev) => {
        const next = prev + 1

        // T+2:00 -> 120s
        if (next === 120) {
          callbacksRef.current.onRiverWarning()
        }
        // T+4:00 -> 240s
        else if (next === 240) {
          callbacksRef.current.onZone7Escalation()
        }
        // T+6:00 -> 360s
        else if (next === 360) {
          if (!approvedRef.current) {
            callbacksRef.current.onAutoExecute()
          }
        }
        // T+8:00 -> 480s
        else if (next === 480) {
          callbacksRef.current.onAllClear()
          setDemoStatus('completed')
          clearInterval(timer)
        }

        return next
      })
    }, 1000)

    return () => clearInterval(timer)
  }, [demoStatus])

  return {
    demoStatus,
    startDemo,
    escalationApproved,
    setEscalationApproved,
    elapsedSeconds,
  }
}
