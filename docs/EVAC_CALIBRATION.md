# Calibrating the evacuation layer against real district data

> **Why this matters most for the evacuation layer.** The prediction models are
> validated on real data; the evacuation *decision* layer is not — its clearance
> times, compliance/participation rates, and casualty rates are explicit,
> **unvalidated planning assumptions** (the code says so wherever they surface).
> Replacing even one set with values fit to a real district's historical
> evacuations is what moves this layer from "research" toward "operational." This
> document is the protocol; `disastermind/evacuation/calibration.py` is the
> harness that does the fitting.

## What the harness does

Given real records of past zone evacuations, `calibrate()`:

1. Scores the **current default** parameters against the observed clearance times
   (mean absolute error in hours, plus signed bias).
2. Fits `mobilization_hours` and an effective `participation` to the observations
   by closed-form least squares (the clearance model is linear in these two
   terms — deterministic, no solver, no network).
3. Reports error **before vs. after** so the gain is explicit, and emits the
   calibrated parameters to feed back into planning.

It is tested to recover known parameters and reduce error on synthetic ground
truth (`tests/test_evac_calibration.py`). It does **not** fabricate data — it
needs the real records below.

## The data to collect (one district is enough to start)

For each historical zone-level evacuation you can document, record:

| Field | Meaning | Typical source |
|---|---|---|
| `zone` | zone / ward / block name | district disaster-management plan |
| `population` | people in the evacuation zone | Census / district records |
| `egress_capacity_pph` | persons/hour the zone's roads can move out | road inventory + engineering estimate |
| `observed_clearance_hours` | **actual** hours from order to zone cleared | post-event after-action report / SRC log |
| `observed_participation` | fraction who actually evacuated (optional) | survey / shelter intake counts |

Cyclone Fani (2019) and Amphan (2020) in Odisha/West Bengal are realistic starting
points — both ran large documented evacuations with after-action reporting.

## CSV schema

```csv
zone,population,egress_capacity_pph,observed_clearance_hours,observed_participation
Puri-coastal,52000,3000,21.5,0.86
Konark,18000,1500,14.0,
Astaranga,24000,1800,16.5,0.79
```

`observed_participation` may be left blank; the harness then fits a single
effective participation across the records.

## Running it

```python
from disastermind.evacuation.calibration import load_records_csv, calibrate

records = load_records_csv("odisha_fani_zones.csv")
result = calibrate(records)

print(f"MAE before: {result.mae_before} h   after: {result.mae_after} h "
      f"({result.improvement_pct()}% better)")
print(f"Fitted mobilization: {result.fitted_mobilization_hours} h")
print(f"Fitted participation: {result.fitted_participation}")
for r in result.per_record:
    print(r["zone"], "obs", r["observed_h"], "pred", r["predicted_h"],
          "resid", r["residual_h"])
```

## Defensible protocol

1. **Hold out** at least one zone (or one event) from the fit and report error on
   it — calibration that's only checked on its own fit data proves nothing.
2. **State the n.** Three zones is a start, not a validation; report how many
   records back the fitted numbers and treat the result as provisional until the
   sample is meaningful.
3. **Keep the assumptions labelled** until calibrated. Do not relabel a parameter
   "validated" off a handful of records — say "fit to N zones from event X."
4. **Version the records.** Commit the CSV (with provenance) so the calibration is
   reproducible, exactly like the prediction fixtures.

## Status

- **Ready now:** the fitting harness, CSV loader, and before/after error reporting
  — tested on synthetic ground truth.
- **Your move:** obtain one district's documented evacuation records and run the
  harness. Until then, every clearance/compliance/casualty figure the system
  emits remains explicitly labelled UNVALIDATED — which is the honest state.
