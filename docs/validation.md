# Model Validation — methodology and evidence

This document describes how DisasterMind's three hazard models are validated,
what evidence each claim rests on, and what remains before any model output is
allowed to influence a real dispatch decision. The generated scorecard lives at
[`disastermind/ml/validation/MODEL_CARD.md`](../disastermind/ml/validation/MODEL_CARD.md)
and is reproduced by running:

```bash
python -m disastermind.ml.validation            # all hazards, full effort
python -m disastermind.ml.validation --hazard flood --fast
python -m disastermind.ml.validation --json     # machine-readable report
```

## 1. Real data only — all three hazards

There is **no synthetic data anywhere in the production pipeline**: not in
training (`disastermind.ml.training.real` builds the training tables from the
same real fixtures the validation scores against) and not in validation. The
legacy synthetic generator survives strictly as a unit-test utility
(`source="synthetic"`).

| Hazard | Outcome data (labels) | Driver data (features) | Window |
| --- | --- | --- | --- |
| Earthquake | USGS FDSN catalog: felt reports, ShakeMap MMI, PAGER alerts, tsunami flags (36.5k events, M4.5+) | event magnitude, depth, location physics | 2013–2017 |
| Flood | GloFAS-ERA5 river-discharge reanalysis at 12 Indian river-basin sites (real flow that occurred) | ERA5 rainfall accumulations, discharge state/trend, seasonality | 2010–2023 |
| Fire | USDA FPA-FOD agency-reported wildfire occurrences, 12 Pacific-NW cells (26k fires region-wide) | ERA5 fire weather: temperature, humidity, wind, dryness | 2012–2018 |

Fixture provenance (sources, licences, the FIRMS→FPA-FOD substitution and the
verified OR+WA coverage audit) is embedded in each fixture's `source` block and
documented in `disastermind/ml/validation/fetch.py`, which rebuilds any fixture
from the public APIs (no keys required).

## 2. Leak-free by construction

* Features at day/event *t* use only information available at *t* — trailing
  rain accumulations, today's discharge, event magnitude/depth. Outcome fields
  (felt reports, MMI, alerts) never appear in a feature vector.
* Splits are **temporal**: train strictly precedes test (quake 2013-15 → 16-17,
  flood 2010-18 → 2019-23, fire 2012-16 → 2017-18).
* Event thresholds (flood q95/q99) derive from **train-period climatology
  only**, so the test period cannot define its own events.
* Operating thresholds and calibrators are fitted on a **calibration split**
  carved out of train; the test set is touched once, to measure.

## 3. The bar is the operational incumbent, not no-skill

Each hazard is compared, with paired-bootstrap significance (1,000-resample
percentile CIs and one-sided p-values), against what an agency could run today:

* **Earthquake:** a GMPE-style magnitude–depth attenuation score, and — on the
  PAGER-free felt label — **USGS PAGER itself**.
* **Flood:** **persistence** (the standard hydrological no-model forecast) and
  seasonal climatology.
* **Fire:** the **Angström fire-danger index**, the classic operational
  formula, computed from the same day's weather.

The incumbent's signal is also *stacked* as a model feature (GMPE score,
discharge/threshold ratio), the standard physics-informed design that makes the
incumbent a floor rather than a competitor the model might mysteriously lose to.

## 4. Decision-point metrics, not just AUC

The report states, per hazard: POD (probability of detection), FAR
(false-alarm ratio), POFD, CSI, frequency bias and HSS **at the operating
threshold** — chosen on the calibration split for a target POD — plus the
cost-minimal threshold under an explicit miss:false-alarm cost ratio
(default 100:1, overridable per deployment). The cost of every miss and false
alarm is therefore a stated policy, not an accident of `p >= 0.5`.

## 5. Blocked spatial + temporal cross-validation

* **Leave-one-region-out:** seismic macro-regions, river basins blocks, PNW
  fire regimes — each held out in turn; the **worst** block AUC is reported
  beside the mean.
* **Rolling-origin:** train through year *Y*, test on *Y+1*, repeated across
  the catalog — every fold respects causality, and the fold sequence doubles
  as the skill-decay curve.

## 5b. Lead time vs POD — actionable warning, not just accuracy

A forecast accurate at t+0 is useless: the water is already at the door. For each
forecast horizon, a detector is trained for "will the threshold be crossed
exactly *h* ahead?" and POD/FAR/AUC are reported at the operating threshold
(`ml/eval/leadtime.py`). The headline is the **actionable lead time** — the
longest horizon still holding POD ≥ 80% — which must exceed the evacuation
**clearance time** (Session B's number) or the warning can't drive an
evacuation. On real flood data the model holds POD ≥ 80% out to ~7 days (AUC
degrading gracefully 0.98 → 0.90, FAR rising at long lead, as expected). The
same module emits the **risk trajectory** (p_event per lead horizon) that is the
agreed interface with the evacuation/decision layer. Earthquakes have no
forecast horizon and are excluded by design.

## 5c. Degraded-input robustness — skill when the sensors are down

A disaster takes out the very instruments the model needs. Holding the trained
model fixed (no mid-disaster retraining), the test set is re-scored with a
rising fraction of inputs failed — dead sensors imputed to the training mean —
and the **graceful-degradation curve** of POD/AUC is reported
(`ml/eval/robustness.py`). The headline is the fraction of inputs we can lose
before POD drops below 70%. On real flood data the model stays useful with up to
~25% of gauges down, then falls off — reported honestly, never claimed robust
where it isn't.

## 6. Calibrated uncertainty

* **Isotonic recalibration** (pool-adjacent-violators) fitted on the
  calibration split; ECE before/after is measured on the untouched test set.
* **Split-conformal prediction sets** with finite-sample coverage ≥ 1−α
  regardless of model miscalibration; empirical coverage, singleton rate and
  abstain rate are reported. An abstaining set (`{0,1}`) is an explicit
  "send a human" signal.

## 6b. Closing the fairness gaps (equalized-odds remediation)

Revealing a gap isn't enough — the audit now also *remediates* it. Per-group
operating thresholds are fitted on the **calibration split** (never test) so each
flagged group reaches the target POD, and the result is reported with the
**false-alarm cost of equity** made explicit (equity is achievable, not free).
On the earthquake label both flagged groups (europe-africa region, the
small-magnitude band) close, at +15% and +3% FAR. On flood, most close; the
Brahmaputra/northeast residual is reported *and classified by cause* — a
threshold can't fix weak ranking, so it's flagged as needing better inputs
(denser upstream gauging / flash-flood features), an actionable finding rather
than a silent fail. For flood this mirrors real practice: every river gauge
already has its own warning threshold.

## 6c. External survey-grade cross-check (GDACS)

Beyond the in-house GloFAS proxy, the flood model is cross-checked against an
**independent** survey-grade source: GDACS (UN OCHA / EC-JRC) declared real
Indian flood events — authoritative outcomes that never entered training. The
report asks whether model risk separates GDACS-declared flood days from quiet
days (AUC) and whether it tracks GDACS severity (Red vs Orange vs quiet). The
separation is modest by construction — GDACS events are country-level while the
sites are specific basins, so it's a temporal national check, not per-basin
localisation — and the report says so. EM-DAT and ReliefWeb were the first
choices but now gate bulk data (login / approved appname); GDACS is the openly
fetchable authoritative substitute (`ml/validation/external.py`,
`fetch.py gdacs`).

## 6d. Full-pipeline historical backtest (shadow mode on the past)

The single most synthesis-level piece: `hindcast/pipeline_backtest.py` replays
the **whole chain** on real cyclones (Fani 2019, Amphan 2020) —
forecast → risk trajectory carrying Session A's *validated* FAR/lead reliability
→ the end-to-end evacuation decision → scored against the documented outcome
(people evacuated, deaths, landfall intensity). Both reach a protective decision
(order-now + vertical shelter) consistent with the real large-scale evacuations
that held the tolls low, and both surface the same transport-dependent equity
gap. Run `python -m disastermind.hindcast --backtest`. Honest scope: the FAR/lead
reliability is validated, but the per-storm probability path is a labelled proxy
for IMD's dynamical forecast, and this is hindcast on documented events — not a
live shadow season.

## 7. Fairness audit

At the shared operational threshold, per declared equity axis (urban/rural
flood sites, basins, regions, magnitude/depth bands), the audit publishes POD /
FAR / AUC / base rate per subgroup and flags any group whose POD falls more
than 5 points below overall (with an n≥30 floor). Flags are published, not
suppressed — a flagged basin is an action item (densify inputs, re-weight
training), and the audit re-runs on every validation pass.

## 8. Rare-severe tail

Severity slices (M6/6.5/7+, ≥1.2× flood threshold, train-q99 severe floods,
100/1000-acre fires) are scored separately with bootstrap confidence intervals,
because the events that matter most are the rarest and an average hides them.
Empty slices ("no M8 in the test window") are reported as empty, never dropped.

## 9. Drift + retraining

Per-feature PSI (reference-decile) and two-sample KS between train and live
windows, with the industry thresholds (0.10 watch / 0.25 drifted), feed an
explicit `retrain_decision` together with the rolling-origin decay curve
(latest-fold AUC > 0.05 below historic mean ⇒ retrain). The decision object
names which signal fired — auditable, not silent.

## 10. Shadow mode — the institutional gate

`disastermind.ml.shadow` provides the final gate: an **append-only,
hash-chained journal** of live predictions committed *before* outcomes are
knowable, outcome attachment, a season scorecard (POD/FAR/AUC/Brier/reliability
plus unresolved counts), and `export_for_review()` — the complete journal +
scorecard in one document for independent peer review. Tampering breaks the
chain and scoring refuses to run. **No model output may influence operations
until at least one full season has been shadow-scored and externally
reviewed.** See the runbook's "Shadow-mode validation" section for the
operational procedure.

## Honest limitations (current)

* The flood outcome is reanalysis discharge exceedance, not surveyed inundation
  extent in km²; the quake damage label uses ShakeMap MMI + PAGER alerts, not
  ground-surveyed damage. Both are real measured/estimated outcomes, but
  post-disaster survey datasets would be stronger still.
* Fire validation covers the Pacific Northwest (the public FPA-FOD layer's
  verified coverage), not India; NASA FIRMS remains the intended Indian source
  when the host is reachable from the deployment network.
* The fairness audit currently flags under-protection in specific basins and
  low-magnitude bands — these are open action items listed in the generated
  report, by design impossible to miss.
* The largest events (M8+, record monsoons) remain sparse in any archive;
  tail CIs are wide and say so.
