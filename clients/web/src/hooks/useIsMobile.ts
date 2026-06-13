import { useEffect, useState } from 'react'

const MOBILE_QUERY = '(max-width: 430px)'
const MOBILE_UA = /Android|iPhone|iPad|iPod|Mobile|webOS|BlackBerry|IEMobile|Opera Mini/i

function evaluate(): boolean {
  if (MOBILE_UA.test(navigator.userAgent)) return true
  return window.matchMedia(MOBILE_QUERY).matches
}

/**
 * Reactive mobile detection. A real phone is matched by user-agent; otherwise we
 * track the viewport so the app responds to resizes instead of locking to a
 * (possibly transient) width sampled once at mount.
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(evaluate)

  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY)
    const update = () => setIsMobile(evaluate())
    mq.addEventListener('change', update)
    window.addEventListener('resize', update)
    update()
    return () => {
      mq.removeEventListener('change', update)
      window.removeEventListener('resize', update)
    }
  }, [])

  return isMobile
}
