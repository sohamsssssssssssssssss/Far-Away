import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import { SYNTHETIC_MAP_STATE, ODISHA_SHELTERS, IMD_ALERTS } from '../../../lib/mapTypes'
import type { MapState, GpsReading, Shelter } from '../../../lib/mapTypes'

// Team status colours
const STATUS_COLOUR: Record<string, string> = {
  active: '#22c55e',
  staged: '#f59e0b',
  distress: '#ef4444',
  offline: '#6b7280',
}

function makeTeamEl(team: GpsReading): HTMLDivElement {
  const el = document.createElement('div')
  el.style.cssText = `
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: ${STATUS_COLOUR[team.status] ?? '#6b7280'};
    border: 2px solid #fff;
    box-shadow: 0 0 6px rgba(0,0,0,0.6);
    cursor: pointer;
    transition: transform 0.6s ease;
  `
  el.title = `${team.team_id} — ${team.status}`
  return el
}

interface LiveMapProps {
  mapState?: MapState
  liveShelters?: Shelter[]
  className?: string
}

export function LiveMap({ mapState, liveShelters, className }: LiveMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markersRef = useRef<Record<string, maplibregl.Marker>>({})
  const [mapReady, setMapReady] = useState(false)

  const state = mapState ?? SYNTHETIC_MAP_STATE

  // Initialise map once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      center: [85.8312, 19.8135], // [lon, lat] — Puri, Odisha
      zoom: 7,
      attributionControl: false,
    })

    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.addControl(
      new maplibregl.AttributionControl({ compact: true }),
      'bottom-right'
    )

    map.on('load', () => {
      // ── Risk heatmap layer ──────────────────────────────────────────
      map.addSource('risk-cells', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })

      map.addLayer({
        id: 'risk-heatmap',
        type: 'heatmap',
        source: 'risk-cells',
        paint: {
          'heatmap-weight': ['get', 'probability'],
          'heatmap-intensity': 1.5,
          'heatmap-radius': 40,
          'heatmap-opacity': 0.7,
          'heatmap-color': [
            'interpolate',
            ['linear'],
            ['heatmap-density'],
            0, 'rgba(0,0,255,0)',
            0.3, 'rgba(0,255,255,0.5)',
            0.6, 'rgba(255,165,0,0.7)',
            1, 'rgba(255,0,0,0.9)',
          ],
        },
      })

      // ── Dispatch route lines ────────────────────────────────────────
      map.addSource('routes', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })

      map.addLayer({
        id: 'route-lines',
        type: 'line',
        source: 'routes',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 2.5,
          'line-opacity': 0.8,
          'line-dasharray': [4, 2],
        },
      })

      // ── IMD Alert circles ──────────────────────────────────────────────────────
      map.addSource('imd-alerts', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: IMD_ALERTS.map(alert => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [alert.lon, alert.lat] },
            properties: {
              id: alert.id,
              severity: alert.severity,
              type: alert.type,
              headline: alert.headline,
              radiusKm: alert.radiusKm,
              color: alert.severity === 'RED' ? '#ef4444'
                : alert.severity === 'ORANGE' ? '#f97316'
                : '#eab308',
            },
          })),
        },
      })

      map.addLayer({
        id: 'imd-alert-fill',
        type: 'circle',
        source: 'imd-alerts',
        paint: {
          'circle-radius': [
            'interpolate', ['linear'], ['zoom'],
            6, ['/', ['get', 'radiusKm'], 2],
            10, ['/', ['get', 'radiusKm'], 0.5],
          ],
          'circle-color': ['get', 'color'],
          'circle-opacity': 0.12,
          'circle-stroke-color': ['get', 'color'],
          'circle-stroke-width': 1.5,
          'circle-stroke-opacity': 0.6,
        },
      })

      // ── Shelter markers ────────────────────────────────────────────────────────
      map.addSource('shelters', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: ODISHA_SHELTERS.map(s => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
            properties: {
              id: s.id,
              name: s.name,
              district: s.district,
              capacity: s.capacity,
              occupied: s.occupied,
              status: s.status,
              pct: Math.round((s.occupied / s.capacity) * 100),
              color: s.status === 'CLOSED' ? '#475569'
                : s.status === 'FULL' ? '#ef4444'
                : s.occupied / s.capacity > 0.8 ? '#f97316'
                : '#22c55e',
            },
          })),
        },
      })

      map.addLayer({
        id: 'shelter-circles',
        type: 'circle',
        source: 'shelters',
        paint: {
          'circle-radius': 8,
          'circle-color': ['get', 'color'],
          'circle-opacity': 0.9,
          'circle-stroke-color': '#0f172a',
          'circle-stroke-width': 1.5,
        },
      })

      // Shelter label — "S"
      map.addLayer({
        id: 'shelter-labels',
        type: 'symbol',
        source: 'shelters',
        layout: {
          'text-field': 'S',
          'text-size': 9,
          'text-font': ['Open Sans Bold', 'Arial Unicode MS Bold'],
          'text-anchor': 'center',
          'text-offset': [0, 0.05],
        },
        paint: {
          'text-color': '#ffffff',
        },
      })

      // Shelter click popup
      map.on('click', 'shelter-circles', (e: any) => {
        const props = e.features[0].properties
        const coords = e.features[0].geometry.coordinates.slice() as [number, number]
        const pct = props.pct
        const statusColor = props.status === 'CLOSED' ? '#475569'
          : props.status === 'FULL' ? '#ef4444'
          : pct > 80 ? '#f97316'
          : '#22c55e'

        new maplibregl.Popup({ closeButton: true, maxWidth: '220px' })
          .setLngLat(coords)
          .setHTML(`
            <div style="font-family:monospace;font-size:11px;line-height:1.6;color:#e2e8f0;padding:4px 2px;">
              <div style="font-weight:700;font-size:12px;margin-bottom:4px;color:#f8fafc;">${props.name}</div>
              <div style="color:#94a3b8;margin-bottom:6px;">${props.district} District</div>
              <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                <span>Status</span>
                <span style="font-weight:700;color:${statusColor}">${props.status}</span>
              </div>
              <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                <span>Occupancy</span>
                <span style="font-weight:700;color:${statusColor}">${props.occupied} / ${props.capacity} (${pct}%)</span>
              </div>
              <div style="background:rgba(255,255,255,0.08);border-radius:3px;height:4px;margin-top:6px;">
                <div style="background:${statusColor};height:4px;border-radius:3px;width:${Math.min(pct,100)}%;"></div>
              </div>
            </div>
          `)
          .addTo(map)
      })

      map.on('mouseenter', 'shelter-circles', () => { map.getCanvas().style.cursor = 'pointer' })
      map.on('mouseleave', 'shelter-circles', () => { map.getCanvas().style.cursor = '' })

      // IMD alert click popup
      map.on('click', 'imd-alert-fill', (e: any) => {
        const props = e.features[0].properties
        const coords = e.features[0].geometry.coordinates.slice() as [number, number]
        const severityColor = props.severity === 'RED' ? '#ef4444'
          : props.severity === 'ORANGE' ? '#f97316' : '#eab308'

        new maplibregl.Popup({ closeButton: true, maxWidth: '240px' })
          .setLngLat(coords)
          .setHTML(`
            <div style="font-family:monospace;font-size:11px;line-height:1.6;color:#e2e8f0;padding:4px 2px;">
              <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
                <span style="font-weight:700;font-size:10px;color:${severityColor};letter-spacing:0.08em;">
                  ⚠ IMD ${props.severity} ALERT
                </span>
              </div>
              <div style="font-weight:600;font-size:11px;margin-bottom:4px;color:#f8fafc;">${props.type.replace('_',' ')}</div>
              <div style="color:#94a3b8;font-size:10px;line-height:1.5;">${props.headline}</div>
              <div style="margin-top:6px;color:#64748b;font-size:9px;">Radius: ${props.radiusKm}km</div>
            </div>
          `)
          .addTo(map)
      })

      map.on('mouseenter', 'imd-alert-fill', () => { map.getCanvas().style.cursor = 'pointer' })
      map.on('mouseleave', 'imd-alert-fill', () => { map.getCanvas().style.cursor = '' })

      setMapReady(true)
    })

    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
      markersRef.current = {}
    }
  }, [])

  // Update shelter markers when liveShelters changes
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    const source = map.getSource('shelters') as maplibregl.GeoJSONSource | undefined
    if (!source || !liveShelters) return

    source.setData({
      type: 'FeatureCollection',
      features: liveShelters.map(s => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
        properties: {
          id: s.id,
          name: s.name,
          district: s.district,
          capacity: s.capacity,
          occupied: s.occupied,
          status: s.status,
          pct: Math.round((s.occupied / s.capacity) * 100),
          color: s.status === 'CLOSED' ? '#475569'
            : s.status === 'FULL' ? '#ef4444'
            : s.occupied / s.capacity > 0.8 ? '#f97316'
            : '#22c55e',
        },
      })),
    })
  }, [mapReady, liveShelters])

  // Update layers when state changes
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current

    // ── Risk heatmap ──────────────────────────────────────────────────
    const riskSource = map.getSource('risk-cells') as maplibregl.GeoJSONSource
    if (riskSource) {
      riskSource.setData({
        type: 'FeatureCollection',
        features: state.riskCells.map(cell => ({
          type: 'Feature',
          geometry: {
            type: 'Point',
            // IMPORTANT: Mapbox/MapLibre wants [lon, lat]
            coordinates: [cell.centroid.lon, cell.centroid.lat],
          },
          properties: {
            probability: cell.probability,
            zone: cell.zone ?? cell.cell_id,
          },
        })),
      })
    }

    // ── Routes ────────────────────────────────────────────────────────
    const routeSource = map.getSource('routes') as maplibregl.GeoJSONSource
    if (routeSource) {
      const ROUTE_COLOURS: Record<string, string> = {
        evacuation: '#f59e0b',
        rescue: '#3b82f6',
        supply: '#22c55e',
      }
      routeSource.setData({
        type: 'FeatureCollection',
        features: state.routes.map(r => ({
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: r.waypoints.map(w => [w.lon, w.lat]),
          },
          properties: {
            team_id: r.team_id,
            color: ROUTE_COLOURS[r.route_type] ?? '#ffffff',
          },
        })),
      })
    }

    // ── Team markers ──────────────────────────────────────────────────
    const existingIds = new Set(Object.keys(markersRef.current))
    const newIds = new Set(Object.keys(state.teams))

    // Remove stale markers
    existingIds.forEach(id => {
      if (!newIds.has(id)) {
        markersRef.current[id].remove()
        delete markersRef.current[id]
      }
    })

    // Add or update markers
    Object.values(state.teams).forEach(team => {
      const lngLat: [number, number] = [team.location.lon, team.location.lat]
      if (markersRef.current[team.team_id]) {
        // Smooth position update
        markersRef.current[team.team_id].setLngLat(lngLat)
        // Update colour
        const el = markersRef.current[team.team_id].getElement()
        el.style.background = STATUS_COLOUR[team.status] ?? '#6b7280'
      } else {
        const el = makeTeamEl(team)
        const marker = new maplibregl.Marker({ element: el })
          .setLngLat(lngLat)
          .setPopup(
            new maplibregl.Popup({ offset: 12 }).setHTML(
              `<div style="color:#000;font-size:12px;font-weight:600">
                ${team.team_id}<br/>
                <span style="color:${STATUS_COLOUR[team.status]}">● ${team.status.toUpperCase()}</span>
              </div>`
            )
          )
          .addTo(map)
        markersRef.current[team.team_id] = marker
      }
    })
  }, [mapReady, state])

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ width: '100%', height: '100%', borderRadius: '8px', overflow: 'hidden' }}
    />
  )
}
