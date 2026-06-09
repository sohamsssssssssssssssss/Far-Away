# DisasterMind — Frontend / Map Message Contract (Group A → Group B)

The exact shapes the operator console + map consume. All examples are the **real
serialized output** of the running backend (not idealized). Field names, types,
and topic strings are stable; code against these.

## 1. WebSocket `/ws`

On connect you receive **one snapshot frame**, then a **live stream** of every
bus message as it is produced:

```jsonc
// frame 1 — snapshot (handle this first; it has no `topic`)
{ "kind": "snapshot", "topics": { "tier2.prediction": 8, "tier3.dispatch": 20, ... } }

// every subsequent frame — a full message envelope (Message.to_dict)
{
  "id": "e06b8e02-...",            // uuid, stable per message
  "topic": "tier2.prediction",     // route on THIS — see §2
  "type": "alert",                  // alert | instruction | query | acknowledgement | escalation
  "priority": 2,                    // int 1..5  (1=critical … 5=info)
  "module": "B",                    // "A" cyclone/flood | "B" earthquake | "C" fire/collapse
  "incident_id": "usgs:us7000sample1",
  "escalation_trigger": null,       // string when type=="escalation"
  "reasoning": ["…human-readable why…"],
  "timestamp": "2026-06-09T06:59:48.741949+00:00",  // ISO-8601 UTC
  "ttl_seconds": 300,
  "sender": "tier2.prediction.earthquake",
  "recipient": "tier2.cascade",
  "payload": { … topic-specific, see §3 … }
}
```
When auth is enabled (`DM_API_KEYS` set) the WS **requires** a token:
`?` is not used — send header `Authorization: Bearer <token>` on the WS upgrade.

## 2. Map layers → source message (where the lat/lon lives)

| Map layer | topic | lat/lon path | value to render |
|-----------|-------|--------------|-----------------|
| **Risk heatmap** | `tier2.prediction` | `payload.risk_cells[].centroid {lat,lon}` | `.probability` (0–1), `.population_at_risk` |
| **Building collapse** (B) | `tier2.prediction` | `payload.buildings[].location` | `.collapse_probability`, `.estimated_trapped` |
| **Team markers** | `tier3.iot_telemetry` (`kind=="gps_beacon"`) | `payload.readings[].location` | `.status` (idle/enroute/onsite/returning), `.asset_type` |
| **Dispatch routes** | `tier3.dispatch` (drop `kind=="dispatch_ack"`) | `payload.order.waypoints[] {lat,lon}` | polyline; `.recipients`, `.body` |
| **Evac routes** | `tier2.routing_plan` | `payload.routes[].waypoints[]` | polyline; `.population_class`, `.shelter_id` |
| **Closed roads** | `tier2.cascade` | *(see §4 note — ids, not geometry)* | `.failures[].reason`, `.cutoff_segments` |

## 3. Payload schemas (real examples)

**`tier2.prediction`** — `payload`:
```jsonc
{ "kind": "risk", "incident_id": "...", "module": "B",
  "shap_features": [ {"feature":"distance_km","value":0.5014,"direction":"up"}, ... ],   // most-influential first
  "risk_cells": [ {"cell_id":"100m:0:0","centroid":{"lat":26.35,"lon":91.95},
                   "probability":0.3065,"horizon_minutes":0,"population_at_risk":2,"shap":{...}} ],
  "buildings": [ {"building_id":"bld-0","location":{"lat":26.35,"lon":91.95},
                  "collapse_probability":0.3065,"estimated_trapped":1,"construction":"unknown"} ],
  "fire_fronts": [] }   // module C: [{horizon_minutes, perimeter:[{lat,lon}], critical_infrastructure:[]}]
```

**`tier3.dispatch`** — `payload` (ignore frames where `kind=="dispatch_ack"`):
```jsonc
{ "channel": "terrestrial",                  // terrestrial | sms | push | iridium | cap | radio
  "recipients": ["NDRF-01"], "body": "DISPATCH NDRF-01 -> …",
  "via": "autonomous",                        // autonomous | auto_execute_on_timeout | approved
  "order": { "team_id":"NDRF-01", "site":"100m:0:0", "asset_id":"HELI-01", "order_id":"DO-...",
             "waypoints":[{"lat":26.35,"lon":91.95}, …], "priority":3, "reason":"…",
             "channel":"terrestrial", "incident_id":"..." } }
```

**`tier3.iot_telemetry`** (`kind=="gps_beacon"`) — `payload`:
```jsonc
{ "kind": "gps_beacon",
  "readings": [ {"team_id":"NDRF-01","asset_type":"ndrf_team",
                 "location":{"lat":20.3,"lon":85.82},"status":"idle"} ] }
```

**`tier2.field_order`** — `payload`:
```jsonc
{ "kind":"field_order", "incident_id":"...",
  "orders":[ {"team_id":"NDRF-01","site":"100m:0:0","waypoints":[{"lat":..,"lon":..}],
              "priority":3,"reason":"…","order_id":"DO-...","asset_id":"HELI-01","channel":"terrestrial"} ],
  "escalation": null }   // or {"trigger":"cross_state_resource_request","summary":"…","scale":1}
```

**`tier2.routing_plan`** — `payload`:
```jsonc
{ "kind":"routing", "incident_id":"...",
  "routes":[ {"route_id":"er-1","vehicle_id":"HELI-01","waypoints":[{"lat":..,"lon":..}],
              "population_class":"general","shelter_id":"shelter.default",
              "depart_after_minute":0,"avoid_reason":""} ] }
// population_class priority: mobility_impaired > elderly > children > hospitalised > general
```

**`tier2.cascade`** — `payload`:
```jsonc
{ "kind":"cascade", "incident_id":"...",
  "failures":[ {"segment_id":"bld-0","fails_at_minute":802,"reason":"high_mmi","viable_until_minute":787} ],
  "cutoff_segments":["bld-0", ...], "safe_windows":{"bld-0":787, ...},
  "aftershock_probability":{"24":1.0,"48":1.0,"72":1.0}, "aftershock_magnitude":5.0 }
// reason ∈ inundation | high_mmi | fire_path
```

## 4. REST endpoints (poll / actions)

- `GET /health` → `{status, commander, messages_seen, pending_escalations}`
- `GET /topics` → `{ "<topic>": <count>, ... }`
- `GET /incidents` → per-incident rollup `[{incident_id, message_count, ...}]`
- `GET /escalations` → `[{report_id, trigger, human_only, deadline_epoch, status, incident_id}]`
- `POST /escalations/{report_id}/approve` · `POST /escalations/{report_id}/reject`
- (Phase 1) list endpoints gain `?limit=&offset=` when the store moves to Postgres.

Auth (when configured): `Authorization: Bearer <token>` or `X-API-Key: <token>`.
Open without a token even under auth: `/`, `/health`, `/docs`.

## 5. Integration notes (read these)

- **CASCADE has no geometry.** `segment_id` / `cutoff_segments` are cell/building
  ids (e.g. `"100m:0:0"`, `"bld-0"`), **not** lat/lon. To draw closed roads, the
  map must resolve ids → coordinates: the matching `cell_id` appears in
  `prediction.risk_cells[].centroid`. True road geometry lives in the backend
  `roadnet` package and is **not on the WS** yet — ask if you need a
  `GET /roads?bbox=` endpoint and I'll add it.
- **`waypoints` is a polyline** `[{lat,lon}, …]`; in synthetic scenarios endpoints
  may coincide (origin≈target) — real data has distinct points.
- **`cell_id` format**: `"<size>m:<row>:<col>"`, e.g. `"100m:0:0"` (100 m grid).
- Coordinates are **WGS84 lat/lon**; render as `[lon, lat]` for Mapbox GL.
