# Live ingestion-adapter status (probed)

> **Local note, not committed.** Correcting the claim that DisasterMind "runs on
> random endpoints / has no real feeds." The `tier3/ingestion/` adapters already
> target authoritative Indian + global sources. Below is each one's *live* status
> as probed directly (no project changes — just `urllib` GETs against the real URLs).

| Source (adapter) | Endpoint | Live status | Verdict |
|---|---|---|---|
| **USGS** quakes (`seismic.py`) | `earthquake.usgs.gov/.../all_hour.geojson` | HTTP 200, valid GeoJSON | ✅ real-ready, key-free |
| **Open-Meteo flood** (`openmeteo.py`) | `flood-api.open-meteo.com/v1/flood` | HTTP 200, valid JSON | ✅ real-ready, key-free |
| **Open-Meteo weather** (`openmeteo.py`) | `api.open-meteo.com/v1/forecast` | HTTP 200, valid JSON | ✅ real-ready, key-free |
| **NASA FIRMS** fire (`wildfire.py`) | `firms.modaps.eosdis.nasa.gov/data/country/...` | HTTP 200, CSV data | ✅ real-ready, key-free (country archive); `api/area` needs free MAP_KEY |
| **India-WRIS** rivers (`hydromet.py`) | `indiawris.gov.in/wris/api/RiverMonitoring/getRiverStations` | domain up (301), **endpoint 404** | ⚠️ path/method changed — needs fix (likely POST/params or new path) |
| **ISRO Bhuvan** flood (`hydromet.py`) | `bhuvan-app1.nrsc.gov.in/api/flood/inundation.json` | domain up (302), **endpoint 404** | ⚠️ needs Bhuvan token + current path |
| **India NCS RISEQ** seismic (`seismic.py`) | `riseq.seismo.gov.in/riseq/earthquake/rss` | domain up (200 root), **path 404** | ⚠️ RSS path moved — needs updating |
| **IMD** (`hydromet.py`) | `mausam.imd.gov.in` API | domain up (200) | 🔑 needs registration + API key + IP whitelist (not key-free) |

## Honest summary
- **4 sources work live and key-free right now** — USGS quakes, Open-Meteo flood,
  Open-Meteo weather, NASA FIRMS country archive. The adapters can pull real data
  today against these.
- **3 Indian-government endpoints** (India-WRIS, Bhuvan, NCS RISEQ) have *up*
  domains but the specific adapter URL paths now 404 — the APIs moved or need
  tokens. These are coded but need endpoint/credential updates before they're live.
- **IMD** is the one true gated source — free but requires registration + key + IP
  whitelist, so it can't be probed anonymously.

**Bottom line:** the "no data" criticism is wrong — the wiring exists and the
authoritative sources are reachable. The real, smaller task is (a) obtaining the
two free keys (FIRMS MAP_KEY, IMD API key) and (b) refreshing three drifted
Indian-gov endpoint paths. That's days of integration work, not a data-availability
wall.
