import { describe, it, expect } from 'vitest'
import { FEED_ADAPTERS, PROVENANCE_SUMMARY } from './feedProvenance'

// The feed-status board's whole point is HONESTY: it shows the real reachability
// (live / degraded / key-required), not an all-green mock. These tests lock that in.

describe('feed provenance', () => {
  it('every adapter has a valid, recognised status', () => {
    const valid = new Set(['live', 'degraded', 'key-required'])
    for (const a of FEED_ADAPTERS) {
      expect(valid.has(a.status), `${a.name} has status ${a.status}`).toBe(true)
      expect(a.name).toBeTruthy()
      expect(a.source).toBeTruthy()
      expect(a.detail).toBeTruthy()
    }
  })

  it('PROVENANCE_SUMMARY counts exactly match the adapter list', () => {
    const live = FEED_ADAPTERS.filter((a) => a.status === 'live').length
    const degraded = FEED_ADAPTERS.filter((a) => a.status === 'degraded').length
    const keyReq = FEED_ADAPTERS.filter((a) => a.status === 'key-required').length
    expect(PROVENANCE_SUMMARY.liveKeyFree).toBe(live)
    expect(PROVENANCE_SUMMARY.degraded).toBe(degraded)
    expect(PROVENANCE_SUMMARY.keyRequired).toBe(keyReq)
    // The three buckets partition the whole list — nothing falls through.
    expect(live + degraded + keyReq).toBe(FEED_ADAPTERS.length)
  })

  it('is honest — not an all-green board', () => {
    // The credibility claim is that some feeds are shown as NOT live. If this ever
    // becomes all-green, that's exactly the dishonesty the panel was built to avoid.
    expect(PROVENANCE_SUMMARY.degraded + PROVENANCE_SUMMARY.keyRequired).toBeGreaterThan(0)
  })

  it('every live, key-free adapter is flagged keyFree', () => {
    for (const a of FEED_ADAPTERS) {
      if (a.status === 'live' && a.detail.toLowerCase().includes('no key')) {
        expect(a.keyFree).toBe(true)
      }
    }
  })
})
