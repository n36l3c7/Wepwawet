import { useEffect, useState } from 'react'

const TICK_MS = 400

/** True when the user asked the OS to reduce motion. */
export function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/** A counter that advances every 400 ms while `active` (and motion is allowed). */
export function useTick(active = true) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!active || prefersReducedMotion()) return undefined
    const timer = setInterval(() => setTick((t) => t + 1), TICK_MS)
    return () => clearInterval(timer)
  }, [active])
  return tick
}
