# Evacuation Clearance-Time Decision

_Zone puri: population 200,500, participation 90%._

## Clearance time (hours to empty the zone)
- mobilization 4.0 h + egress-queue 8.58 h + last-mile 0.75 h = **13.33 h** (at 21,030 persons/h egress)

## Decision vs the forecast's warning
- Forecast actionable lead: **72 h**
- **FEASIBLE** — Feasible: issue the order by T-minus-13.3 h; forecast buys 72 h of warning -> 58.7 h slack.

## Sensitivity to egress capacity (the dominant uncertainty)
| Egress (persons/h) | Clearance (h) | Feasible vs lead |
|---|---|---|
| 2,000 | 95.0 | ❌ |
| 5,000 | 40.8 | ✅ |
| 10,000 | 22.8 | ✅ |
| 20,000 | 13.8 | ✅ |
| 40,000 | 9.3 | ✅ |

## Honest limits
- Egress capacity is the dominant uncertainty and is NOT precisely derivable from OSM; the sensitivity above is the real answer. A deployment needs surveyed evacuation-route capacities.
- Mobilization lag and participation are planning assumptions; compliance (whether people actually leave) is modelled separately, not assumed here.
- Applies to cyclone/flood/fire only; earthquakes are impact-triage, not evacuation forecasting.
