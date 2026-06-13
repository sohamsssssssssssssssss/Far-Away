# National Cyclone Backtest — all real landfalling NI-basin storms

_Source: NOAA IBTrACS v04r01 landfalling subset · 92 storms · 72 h forecast cutoff._

- **92** real storms · **32** Indian-coast landfalls · activation rate **65%** (31 activated, 44 unknown wind record)

## By coastal region
| Region | Storms | Activated | Activation rate | Unknown |
|---|---|---|---|---|
| Other / open-coast | 38 | 10 | 77% | 25 |
| Tamil Nadu / Puducherry | 11 | 6 | 60% | 1 |
| Bangladesh | 9 | 1 | 20% | 4 |
| Andhra Pradesh | 8 | 2 | 33% | 2 |
| Myanmar | 5 | 3 | 100% | 2 |
| Oman / Arabia | 4 | 1 | 33% | 1 |
| Sri Lanka | 4 | 1 | 100% | 3 |
| West Bengal / Sundarbans | 4 | 2 | 100% | 2 |
| Odisha | 4 | 2 | 100% | 2 |
| Gujarat | 3 | 3 | 100% | 0 |
| Maharashtra / Konkan | 2 | 0 | 0% | 2 |

## Honest limits
- 92 real landfalling NI-basin cyclones (IBTrACS v04r01); 32 classified to an Indian coastal region, the rest to neighbouring coasts (Bangladesh/Myanmar/Sri Lanka/…) — classified honestly, not forced.
- Region = approximate bounding box, NOT official state polygons.
- Activation = IMD cyclonic-storm alert (>=34 kt) present before the 72 h cutoff; storms with no usable pre-cutoff wind are 'unknown', never counted as activated.
- This measures coordination-window coverage, not track-forecast skill (IMD's dynamical forecast is the production input).
