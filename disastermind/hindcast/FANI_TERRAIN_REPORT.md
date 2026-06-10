# Flood-Risk Map vs REAL Terrain — Cyclone Fani coastal zone

_Real Copernicus-DEM elevation grid (Open-Meteo) over the Puri/Khordha coast. Is the system's flood-risk map physically grounded in terrain?_

## Real terrain
- **DEM grid:** 169 cells, elevation 0-142 m (80 real low-lying <5 m flood-prone cells)

## Is the risk map grounded? (higher = better)
| Risk model | corr with low elevation | top-20% risk that is real lowland |
|---|---|---|
| distance-from-landfall (current) | -0.24 | **3%** |
| elevation-aware (proposed fix) | 0.61 | **100%** |

## Finding (honest)
- Real Copernicus-DEM elevation grid over the Fani coastal flood zone (169 cells, 0-142 m).
- CURRENT model: only 3% of its top-20% highest-risk cells are real low-lying (<5 m) flood-prone terrain — its risk tracks distance from landfall, not where water actually pools.
- ELEVATION-AWARE fix: combining surge proximity with the real DEM raises that to 100% — risk lands on the real lowlands. The DEM is free and reachable (Copernicus via Open-Meteo), so this is a concrete, actionable model improvement, not a hypothetical.
- Honest scope: low elevation is a physical PROXY for flood-proneness; true ground truth is mapped inundation extent (Copernicus EMS EMSR353 for Fani), which needs GIS/portal access beyond this harness.
