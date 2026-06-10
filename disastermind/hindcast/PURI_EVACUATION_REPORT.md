# Evacuation-Plan Feasibility on REAL Infrastructure — Puri (Cyclone Fani zone)

_Source: OpenStreetMap via Overpass API. Real road network + real shelter buildings of Puri, Odisha (Cyclone Fani landfall/evacuation zone)._

## Real infrastructure
- **Road network:** 24,020 junctions from 2,427 OSM road ways
- **Shelters:** 14 real tagged buildings (e.g. Sanskrit College, Jagannath Temple, Gundicha Temple, S C S Collage)

## The system's routing on the real ground
- **Coverage:** 80/80 sampled coastal at-risk junctions reach a real shelter over the real road network (**100.0%** feasible)
- **Road vs straight-line:** real road distance is **1.462x** the straight-line distance on average (p90 1.686x) — the concrete cost the naive allocator's straight-line assumption ignored
- **Evacuation time:** longest route 45 min at 15 km/h — ✅ within the 24 h pre-landfall lead window

## Honest limits
- Real road network: 24020 junctions from 2427 OSM ways.
- 14 real shelter buildings tagged in OSM (sparse — coastal India under-tags shelters; the real Fani evacuation used the much larger OSDMA multipurpose-cyclone-shelter network). These are candidate real shelters, not the historical Fani assignments (those logs are not public).
- Detour: real road distance averages 1.46x the straight-line distance the naive allocator assumed — the concrete cost of not being road-aware.
- This validates route *feasibility on real infrastructure*, not the quality of the plan against the real Fani evacuation (shelter occupancy, exact routes, and capacities from 2019 are not public).
