// Live data-feed provenance — the real probe result (see ADAPTER_STATUS.md).
// The honest red/yellow IS the credibility: a status board that shows what is
// actually reachable beats an all-green fake.

import type { FeedAdapter } from './types'

export const FEED_ADAPTERS: FeedAdapter[] = [
  {
    name: 'USGS — Earthquakes',
    source: 'earthquake.usgs.gov',
    endpoint: '/earthquakes/feed/v1.0/summary/all_hour.geojson',
    status: 'live',
    detail: 'HTTP 200, valid GeoJSON. Real-ready, no key.',
    keyFree: true,
  },
  {
    name: 'Open-Meteo — Flood (GloFAS)',
    source: 'flood-api.open-meteo.com',
    endpoint: '/v1/flood',
    status: 'live',
    detail: 'HTTP 200, valid JSON. Live GloFAS discharge, no key.',
    keyFree: true,
  },
  {
    name: 'Open-Meteo — Weather',
    source: 'api.open-meteo.com',
    endpoint: '/v1/forecast',
    status: 'live',
    detail: 'HTTP 200, valid JSON. Live forecast, no key.',
    keyFree: true,
  },
  {
    name: 'NASA FIRMS — Active fire',
    source: 'firms.modaps.eosdis.nasa.gov',
    endpoint: '/data/country/viirs-snpp/{year}/..._India.csv',
    status: 'live',
    detail: 'HTTP 200, CSV data. Country archive is key-free; the /api/area '
      + 'endpoint needs a free MAP_KEY.',
    keyFree: true,
  },
  {
    name: 'India-WRIS — River monitoring',
    source: 'indiawris.gov.in',
    endpoint: '/wris/api/RiverMonitoring/getRiverStations',
    status: 'degraded',
    detail: 'Domain up (301) but this endpoint 404s — API path moved or now '
      + 'requires POST/params. Needs an endpoint refresh.',
    keyFree: false,
  },
  {
    name: 'ISRO Bhuvan — Flood inundation',
    source: 'bhuvan-app1.nrsc.gov.in',
    endpoint: '/api/flood/inundation.json',
    status: 'degraded',
    detail: 'Domain up (302) but endpoint 404s — needs a Bhuvan token and the '
      + 'current path.',
    keyFree: false,
  },
  {
    name: 'India NCS — Seismic (RISEQ)',
    source: 'riseq.seismo.gov.in',
    endpoint: '/riseq/earthquake/rss',
    status: 'degraded',
    detail: 'Domain up (200) but the RSS path 404s — feed path moved, needs '
      + 'updating.',
    keyFree: false,
  },
  {
    name: 'IMD — Forecasts / warnings',
    source: 'mausam.imd.gov.in',
    endpoint: 'IMD API portal',
    status: 'key-required',
    detail: 'Domain up. Free, but requires registration + API key + IP '
      + 'whitelist — cannot be probed anonymously.',
    keyFree: false,
  },
]

export const PROVENANCE_SUMMARY = {
  liveKeyFree: FEED_ADAPTERS.filter((a) => a.status === 'live').length,
  degraded: FEED_ADAPTERS.filter((a) => a.status === 'degraded').length,
  keyRequired: FEED_ADAPTERS.filter((a) => a.status === 'key-required').length,
}
