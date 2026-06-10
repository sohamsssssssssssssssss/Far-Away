# Shelter Capacity vs Real Population — Puri (Cyclone Fani zone)

_Real at-risk population vs shelter capacity derived from real OSM building footprints. Place: Puri, Odisha._

## The real numbers
- **Population (at risk):** 200,500 — OSM city population tag (Census-2011-derived)
- **Shelters:** 13 real buildings, 98,290 m^2 total footprint
- **Largest:** S C S Collage (47,505 m^2), Gundicha Temple (21,190 m^2), Sanskrit College (16,277 m^2)

## Capacity vs need
| Density standard | Capacity | Covers | Shortfall |
|---|---|---|---|
| Sphere min (3.5 m^2/person) | 28,082 | **14.0%** | 172,418 |
| Packed cyclone (1.5 m^2/person) | 65,526 | **32.7%** | 134,974 |

- **System flags a shelter resource gap:** ✅ yes (correct — capacity < population)

## What this means (honest)
- Capacity from REAL OSM building footprints (98,290 m^2 across 13 shelters) at published densities; footprint is a single-storey proxy for usable floor area (a limit — multi-storey buildings hold more).
- The gap is largely a DATA gap: OSM under-tags shelters. The real Fani evacuation used the OSDMA multipurpose-cyclone-shelter network (~800+ purpose-built shelters) — not in OSM — which is why the documented toll was 64 deaths. Deployment requires loading the OSDMA shelter registry.
- Flagging this shortfall is CORRECT behaviour: the system should request mutual aid / activate more shelters when capacity falls short, not fail silently.
