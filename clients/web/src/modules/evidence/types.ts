// Shared types for the EVIDENCE module (cyclone backtest, feed provenance,
// shadow journal). These mirror the data contracts produced by the backend/data
// lane — consumed as documented JSON, never imported across the lane boundary.

export interface CycloneStorm {
  sid: string
  name: string
  season: number
  landfall_time: string
  landfall_lat: number
  landfall_lon: number
  region: string
  max_wind_kt: number | null
  cutoff_wind_kt: number | null
  activated: boolean | null // null = "unknown" (no pre-cutoff wind record)
}

export interface CycloneRegion {
  region: string
  storms: number
  activated: number
  unknown: number
  activation_rate: number
}

export interface CycloneBacktest {
  lead_hours: number
  total_storms: number
  india_landfalls: number
  activated: number
  unknown: number
  activation_rate: number
  regions: CycloneRegion[]
  storms: CycloneStorm[]
  notes?: string | string[]
}

export type FeedStatus = 'live' | 'degraded' | 'key-required'

export interface FeedAdapter {
  name: string
  source: string
  endpoint: string
  status: FeedStatus
  detail: string
  keyFree: boolean
}

export interface ShadowRecord {
  kind: 'prediction' | 'outcome'
  payload: Record<string, unknown>
  hash: string
}

export interface ShadowJournalDoc {
  _note?: string
  genesis: string
  records: ShadowRecord[]
}
