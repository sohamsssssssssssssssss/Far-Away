# Hindcast — Cyclone Fani (2019)

_Source: NOAA IBTrACS v04r01. Replayed leak-free: each row uses only best-track data available before its forecast cutoff._

## The real event (documented)
- **Landfall:** 2019-05-03 ~~08:00 IST, near Puri, Odisha (20.2, 85.9)
- **Intensity:** Extremely Severe Cyclonic Storm — sustained 175-185 (gusts to 200+) km/h
- **Outcome:** 64 deaths · ~1.2-1.5 million (Odisha, one of India largest pre-cyclone evacuations) · $8.1B damage
- **Sources:** IMD RSMC New Delhi cyclone report (Fani, 2019); EM-DAT The International Disaster Database; Odisha Special Relief Commissioner

## Hindcast at decreasing lead time
| Lead | Cutoff (UTC) | Intensity | Landfall error | Activated | Plan produced | Dispatches |
|---|---|---|---|---|---|---|
| 72 h | 2019-04-30 06:00:00 | 80 kt | 786 km | ✅ | ✅ | 180 |
| 48 h | 2019-05-01 06:00:00 | 95 kt | 441 km | ✅ | ✅ | 180 |
| 36 h | 2019-05-01 18:00:00 | 100 kt | 280 km | ✅ | ✅ | 180 |
| 24 h | 2019-05-02 06:00:00 | 110 kt | 25 km | ✅ | ✅ | 180 |
| 12 h | 2019-05-02 18:00:00 | 115 kt | 82 km | ✅ | ✅ | 180 |

## What this shows (and doesn't)
- **Activation lead time** is the load-bearing result: Fani's low toll (64 deaths despite a Category-4 strike) was bought by a ~3-day pre-landfall evacuation of ~1.2-1.5M. The system activates on the IMD cyclonic-storm alert days ahead — i.e. it would have triggered the coordination window that mattered.
- **Landfall error** uses a deliberately naive great-circle extrapolation, NOT a dynamical forecast. In production IMD's track forecast is the input; this is a floor on how far even a trivial extrapolation lands from the coast.
- **Honest limits:** DisasterMind is a coordination system, not a track-forecast model; this replay does not re-predict casualties or validate the *quality* of the evacuation plan against the real one — only that activation + a plan would have been produced in time. Validating the plan itself needs the real evacuation/road/shelter records.
