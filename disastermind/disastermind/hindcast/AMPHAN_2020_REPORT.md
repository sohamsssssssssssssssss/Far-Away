# Hindcast — Cyclone Amphan (2020)

_Source: NOAA IBTrACS v04r01. Replayed leak-free: each row uses only best-track data available before its forecast cutoff._

## The real event (documented)
- **Landfall:** 2020-05-20 ~~16:30-17:00 IST, near Bakkhali / Sundarbans, West Bengal (and adjacent Bangladesh) (22.1, 88.4)
- **Intensity:** Very Severe Cyclonic Storm (weakened from Super Cyclonic Storm peak ~240 km/h) — sustained 155-165 (gusts ~185) km/h
- **Outcome:** 128 deaths · ~4.9 million (West Bengal/Odisha + Bangladesh — one of the largest cyclone evacuations) · $13.9B damage
- **Sources:** IMD RSMC New Delhi cyclone report (Amphan, 2020); EM-DAT The International Disaster Database; West Bengal/Bangladesh govt reports

## Hindcast at decreasing lead time
| Lead | Cutoff (UTC) | Intensity | Landfall error | Activated | Plan produced | Dispatches |
|---|---|---|---|---|---|---|
| 72 h | 2020-05-17 12:00:00 | 70 kt | 618 km | ✅ | ✅ | 180 |
| 48 h | 2020-05-18 12:00:00 | 125 kt | 426 km | ✅ | ✅ | 180 |
| 36 h | 2020-05-19 00:00:00 | 125 kt | 237 km | ✅ | ✅ | 180 |
| 24 h | 2020-05-19 12:00:00 | 105 kt | 168 km | ✅ | ✅ | 180 |
| 12 h | 2020-05-20 00:00:00 | 95 kt | 135 km | ✅ | ✅ | 180 |

## What this shows (and doesn't)
- **Activation lead time** is the load-bearing result: Fani's low toll (128 deaths despite a Category-4 strike) was bought by a ~3-day pre-landfall evacuation of ~1.2-1.5M. The system activates on the IMD cyclonic-storm alert days ahead — i.e. it would have triggered the coordination window that mattered.
- **Landfall error** uses a deliberately naive great-circle extrapolation, NOT a dynamical forecast. In production IMD's track forecast is the input; this is a floor on how far even a trivial extrapolation lands from the coast.
- **Honest limits:** DisasterMind is a coordination system, not a track-forecast model; this replay does not re-predict casualties or validate the *quality* of the evacuation plan against the real one — only that activation + a plan would have been produced in time. Validating the plan itself needs the real evacuation/road/shelter records.
