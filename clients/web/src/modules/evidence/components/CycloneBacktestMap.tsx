import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import type { CycloneBacktest } from '../types'

// Approximate coastal bounding boxes for the named regions. These are rough
// rectangles for visual context only — the dataset itself states "Region =
// approximate bounding box, NOT official state polygons", and the UI repeats it.
// [west, south, east, north]
const REGION_BOX: Record<string, [number, number, number, number]> = {
  'Tamil Nadu / Puducherry': [78.5, 8.0, 80.5, 13.5],
  'Andhra Pradesh': [80.0, 13.5, 85.2, 19.0],
  Odisha: [84.5, 18.5, 87.6, 21.6],
  'West Bengal / Sundarbans': [87.4, 21.0, 89.6, 22.6],
  Gujarat: [68.4, 20.4, 73.2, 23.6],
  'Maharashtra / Konkan': [72.5, 15.4, 74.2, 20.1],
  Bangladesh: [89.0, 21.0, 92.6, 23.6],
  Myanmar: [92.4, 15.4, 95.2, 21.2],
  'Sri Lanka': [79.5, 5.9, 82.0, 9.9],
  'Oman / Arabia': [56.0, 16.0, 60.2, 23.2],
  // "Other / open-coast" is intentionally not boxed — it is not a region.
}

// activation_rate -> colour (red 0.0 → amber 0.5 → cyan 1.0)
function rateColour(rate: number): string {
  if (rate >= 0.8) return '#00e5ff'
  if (rate >= 0.5) return '#ffc948'
  if (rate >= 0.25) return '#ff8a4c'
  return '#ff562c'
}

const STATUS_COLOUR = {
  activated: '#00e5ff',
  not: '#ff562c',
  unknown: '#ffc948',
} as const

export function CycloneBacktestMap() {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [data, setData] = useState<CycloneBacktest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  // Load the committed backtest fixture (served from /public/data).
  useEffect(() => {
    const base = import.meta.env.BASE_URL || '/'
    fetch(`${base}data/national_cyclone_backtest.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  // Init map once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      center: [83, 16], // North Indian Ocean
      zoom: 3.4,
      attributionControl: false,
    })
    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
    map.on('load', () => setReady(true))
    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Draw region boxes + landfall points once both map and data are ready.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !data) return

    const regionRate: Record<string, number> = {}
    data.regions.forEach((r) => (regionRate[r.region] = r.activation_rate))

    const regionFeatures = Object.entries(REGION_BOX).map(([region, b]) => ({
      type: 'Feature' as const,
      properties: {
        region,
        rate: regionRate[region] ?? 0,
        colour: rateColour(regionRate[region] ?? 0),
      },
      geometry: {
        type: 'Polygon' as const,
        coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]],
      },
    }))

    const pointFeatures = data.storms.map((s) => {
      const status = s.activated === null ? 'unknown' : s.activated ? 'activated' : 'not'
      return {
        type: 'Feature' as const,
        properties: {
          status,
          colour: STATUS_COLOUR[status],
          radius: 4 + Math.sqrt(s.max_wind_kt ?? 35) * 1.1,
          label: `${s.name} (${s.season}) · ${s.region}`,
          wind: s.max_wind_kt ?? 0,
        },
        geometry: { type: 'Point' as const, coordinates: [s.landfall_lon, s.landfall_lat] },
      }
    })

    if (!map.getSource('regions')) {
      // Sources first, then layers (a layer cannot reference a missing source).
      map.addSource('regions', { type: 'geojson', data: { type: 'FeatureCollection', features: regionFeatures } })
      map.addSource('points', { type: 'geojson', data: { type: 'FeatureCollection', features: pointFeatures } })
      map.addLayer({
        id: 'region-fill', type: 'fill', source: 'regions',
        paint: { 'fill-color': ['get', 'colour'], 'fill-opacity': 0.16 },
      })
      map.addLayer({
        id: 'region-line', type: 'line', source: 'regions',
        paint: { 'line-color': ['get', 'colour'], 'line-width': 1.2, 'line-dasharray': [2, 2] },
      })
      map.addLayer({
        id: 'storm-points', type: 'circle', source: 'points',
        paint: {
          'circle-radius': ['get', 'radius'],
          'circle-color': ['match', ['get', 'status'], 'unknown', 'rgba(255,201,72,0.12)', ['get', 'colour']],
          'circle-opacity': 0.8,
          'circle-stroke-width': ['match', ['get', 'status'], 'unknown', 1.6, 1],
          'circle-stroke-color': ['get', 'colour'],
        },
      })
    } else {
      ;(map.getSource('points') as maplibregl.GeoJSONSource).setData({ type: 'FeatureCollection', features: pointFeatures })
    }

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false })
    map.on('mouseenter', 'storm-points', (e) => {
      map.getCanvas().style.cursor = 'pointer'
      const f = e.features?.[0]
      if (!f) return
      const p = f.properties as { label: string; wind: number; status: string }
      popup
        .setLngLat((f.geometry as { coordinates: [number, number] }).coordinates)
        .setHTML(
          `<strong>${p.label}</strong><br/>max wind ${p.wind} kt · <span style="text-transform:uppercase">${p.status === 'not' ? 'no alert' : p.status}</span>`,
        )
        .addTo(map)
    })
    map.on('mouseleave', 'storm-points', () => {
      map.getCanvas().style.cursor = ''
      popup.remove()
    })
  }, [ready, data])

  const headline = useMemo(() => {
    if (!data) return null
    const known = data.total_storms - data.unknown
    return { ...data, known }
  }, [data])

  return (
    <div className="evidence-pane cyclone-pane">
      <div className="evidence-head">
        <h2>Cyclone Backtest — North Indian Ocean</h2>
        <p className="evidence-sub">
          {headline
            ? `${headline.total_storms} real landfalling cyclones (IBTrACS v04r01) · ${headline.india_landfalls} classified to an Indian coastal region · activation alert ${headline.lead_hours} h pre-landfall`
            : 'Loading…'}
        </p>
      </div>

      <div className="cyclone-body">
        <div className="map-wrap">
          {error && <div className="evidence-error">Could not load backtest data: {error}</div>}
          <div ref={containerRef} className="evidence-map" />
          <div className="map-legend">
            <span><i className="dot" style={{ background: STATUS_COLOUR.activated }} /> alert active pre-cutoff</span>
            <span><i className="dot" style={{ background: STATUS_COLOUR.not }} /> no alert</span>
            <span><i className="dot ring" style={{ borderColor: STATUS_COLOUR.unknown }} /> unknown (no pre-cutoff wind)</span>
            <span className="legend-note">point size ∝ max wind</span>
          </div>
        </div>

        <aside className="cyclone-side">
          {headline && (
            <div className="stat-row">
              <div className="stat"><b>{headline.activated}</b><span>activated</span></div>
              <div className="stat"><b>{(headline.activation_rate * 100).toFixed(0)}%</b><span>activation rate*</span></div>
              <div className="stat"><b>{headline.unknown}</b><span>unknown</span></div>
            </div>
          )}
          <table className="region-table">
            <thead><tr><th>Region</th><th>n</th><th>act</th><th>unk</th><th>rate*</th></tr></thead>
            <tbody>
              {data?.regions.map((r) => (
                <tr key={r.region}>
                  <td><i className="swatch" style={{ background: rateColour(r.activation_rate) }} />{r.region}</td>
                  <td>{r.storms}</td><td>{r.activated}</td><td>{r.unknown}</td>
                  <td>{(r.activation_rate * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="honesty-note">
            * Activation rate = alerts raised ÷ storms with a usable pre-cutoff wind
            record (unknowns excluded, never counted as activated). Region boxes are
            <strong> approximate bounding boxes, not official state polygons.</strong>
            This measures coordination-window coverage, not track-forecast skill.
          </p>
        </aside>
      </div>
    </div>
  )
}
