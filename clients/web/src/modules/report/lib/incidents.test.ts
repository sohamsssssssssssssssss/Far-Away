import { describe, it, expect } from 'vitest'
import { incidents, reportSections, audiences } from './incidents'

// The post-incident report is built from this data; integrity bugs here (e.g.
// reaching more people than were at risk) would surface as nonsensical reports.

describe('incident data integrity', () => {
  it('has incidents with unique ids', () => {
    expect(incidents.length).toBeGreaterThan(0)
    const ids = incidents.map((i) => i.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('never reaches more civilians than were at risk', () => {
    for (const i of incidents) {
      expect(i.civiliansReached, i.id).toBeLessThanOrEqual(i.civiliansAtRisk)
    }
  })

  it('has non-negative, sensible counts', () => {
    for (const i of incidents) {
      expect(i.durationHours, i.id).toBeGreaterThan(0)
      expect(i.zones, i.id).toBeGreaterThan(0)
      expect(i.teamsDeployed, i.id).toBeGreaterThan(0)
      expect(i.autonomousDecisions, i.id).toBeGreaterThanOrEqual(0)
      expect(i.humanOverrides, i.id).toBeGreaterThanOrEqual(0)
      // Overrides are a subset of decisions — can't override more than were made.
      expect(i.humanOverrides, i.id).toBeLessThanOrEqual(i.autonomousDecisions)
    }
  })

  it('exposes report sections and audiences for the report UI', () => {
    expect(reportSections.length).toBeGreaterThan(0)
    expect(audiences.length).toBeGreaterThan(0)
  })
})
