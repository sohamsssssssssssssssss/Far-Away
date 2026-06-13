# DisasterMind — Real-Data Validation (all hazards)

_real data only; leak-free features; temporal + blocked-spatial validation; thresholds and calibrators fitted on calibration splits, never on test_

## Earthquake

- **Source:** USGS FDSN historical catalog 2013-2017 (real earthquakes, M4.5+)
- **Label:** damage-grade outcome: ShakeMap MMI>=VI or PAGER alert>=yellow
- **Split:** temporal: train 2013-2015, test 2016-2017 (out-of-sample)
- **Train:** 22697 rows (base rate 1.37%) · **Test:** 13812 rows (base rate 1.41%)

**Headline (calibrated, out-of-sample):** AUC 0.9372 · Brier 0.0107 · ECE 0.002

### vs operational baselines (paired bootstrap on AUC)
| Baseline | Baseline AUC | Model AUC | dAUC [95% CI] | p | significant |
|---|---|---|---|---|---|
| gmpe_attenuation | 0.9587 | 0.9574 | -0.0012 [-0.0086, +0.0061] | 0.6414 | no |

### At the operating point (threshold chosen on calibration split)
- Target POD 90% -> threshold 0.030
- On test: POD 86.15%, FAR 86.53%, CSI 0.132, bias 6.39 (tp=168, fp=1079, fn=27)
- Cost model (miss:false-alarm = 100:1): test cost 3779 at the cost-optimal threshold

### Calibration + conformal coverage
- ECE raw 0.2102 -> calibrated 0.0020 (isotonic, fit on calibration split)
- Conformal: target coverage 90%, empirical 88.39%, singleton rate 98.49%

### Blocked generalisation
- Leave-one-region-out: worst AUC 0.8271, mean 0.9338 over 4 regions
- Rolling origin: worst AUC 0.946, mean 0.9541 over 3 years

### Fairness audit (shared threshold)
- **region**: FLAGGED (under-protected: europe-africa)
  - remediation (per-group thresholds, fit on calibration): PASS — cost of equity: europe-africa +11% FAR
- **magnitude_band**: FLAGGED (under-protected: mag:4.5-5.5)
  - remediation (per-group thresholds, fit on calibration): still flagged: mag:4.5-5.5 — cost of equity: mag:4.5-5.5 +2% FAR
    - _mag:4.5-5.5: discrimination deficit — needs better inputs/features (threshold cannot fix)_
- **depth_band**: PASS (under-protected: none)

### Rare-severe tail
- M6.0+: 90 events, POD 100.00% [100.00%, 100.00%]
- M6.5+: 46 events, POD 100.00% [100.00%, 100.00%]
- M7.0+: 17 events, POD 100.00% [100.00%, 100.00%]

### Degraded-input robustness (fixed model, sensors failing)
- Graceful until POD 70%: **25%** of inputs down
| Inputs lost | POD | FAR | AUC |
|---|---|---|---|
| 0% | 90.26% | 90.04% | 0.9574 |
| 25% | 75.90% | 88.05% | 0.9338 |
| 50% | 66.15% | 84.36% | 0.9062 |
| 75% | 16.92% | 78.71% | 0.6923 |

### Drift + retraining
- Retrain now: **False** (no drift signal fired)
- _Note: PAGER is excluded as a baseline on this label because the label partially derives from PAGER; see felt_vs_pager for that comparison._

### Incumbent check: felt label vs USGS PAGER
- Model AUC 0.7299 vs PAGER 0.5094 (delta +0.2206, p=0.0040)

## Flood

- **Source:** GloFAS-ERA5 river-discharge reanalysis + ERA5 precipitation, 12 Indian river-basin sites, 2010-2023 daily (real outcomes)
- **Label:** discharge reaches the site's train-climatology q95 flood threshold within the next 1-3 days
- **Split:** temporal: train 2010-2018, test 2019-2023 (five held-out monsoons)
- **Train:** 39084 rows (base rate 7.09%) · **Test:** 21828 rows (base rate 5.95%)

**Headline (calibrated, out-of-sample):** AUC 0.9437 · Brier 0.0278 · ECE 0.0037

### vs operational baselines (paired bootstrap on AUC)
| Baseline | Baseline AUC | Model AUC | dAUC [95% CI] | p | significant |
|---|---|---|---|---|---|
| persistence | 0.9338 | 0.9451 | +0.0115 [+0.0088, +0.0146] | 0.0040 | YES |
| seasonal_climatology | 0.8551 | 0.9451 | +0.0898 [+0.0802, +0.0977] | 0.0040 | YES |

### At the operating point (threshold chosen on calibration split)
- Target POD 90% -> threshold 0.063
- On test: POD 88.83%, FAR 75.36%, CSI 0.239, bias 3.60 (tp=1153, fp=3526, fn=145)
- Cost model (miss:false-alarm = 100:1): test cost 11662 at the cost-optimal threshold

### Calibration + conformal coverage
- ECE raw 0.1760 -> calibrated 0.0037 (isotonic, fit on calibration split)
- Conformal: target coverage 90%, empirical 91.45%, singleton rate 87.55%

### Blocked generalisation
- Leave-one-region-out: worst AUC 0.886, mean 0.9441 over 5 regions
- Rolling origin: worst AUC 0.916, mean 0.9437 over 12 years

### Fairness audit (shared threshold)
- **setting**: PASS (under-protected: none)
- **region**: FLAGGED (under-protected: region:north, region:northeast)
  - remediation (per-group thresholds, fit on calibration): still flagged: region:northeast — cost of equity: region:north +13% FAR, region:northeast +3% FAR
    - _region:northeast: residual threshold gap (consider a lower group threshold / more data)_
- **basin**: FLAGGED (under-protected: basin:brahmaputra, basin:yamuna)
  - remediation (per-group thresholds, fit on calibration): still flagged: basin:brahmaputra — cost of equity: basin:brahmaputra +5% FAR, basin:yamuna +13% FAR
    - _basin:brahmaputra: residual threshold gap (consider a lower group threshold / more data)_

### Rare-severe tail
- peak >=1.2x flood threshold: 806 events, POD 88.83% [86.48%, 90.82%]
- severe (train q99): 430 events, POD 90.23% [86.51%, 93.26%]

### Lead time vs POD (actionable warning)
- Actionable lead time at POD 80%: **168 h**
| Lead (h) | POD | FAR | AUC | events |
|---|---|---|---|---|
| 24 | 89.56% | 39.53% | 0.9809 | 948 |
| 48 | 88.08% | 74.77% | 0.9553 | 948 |
| 72 | 88.50% | 81.66% | 0.9372 | 948 |
| 120 | 89.45% | 84.82% | 0.9192 | 948 |
| 168 | 89.14% | 86.19% | 0.9043 | 948 |

### Degraded-input robustness (fixed model, sensors failing)
- Graceful until POD 70%: **25%** of inputs down
| Inputs lost | POD | FAR | AUC |
|---|---|---|---|
| 0% | 90.06% | 76.67% | 0.9451 |
| 25% | 76.35% | 72.71% | 0.9229 |
| 50% | 55.86% | 67.75% | 0.8919 |
| 75% | 17.03% | 60.54% | 0.8026 |

### External cross-check vs GDACS declared floods (survey-grade)
- AUC separating declared-flood days from quiet days: **0.5653** (436/1819 declared, label external to the model)
- Severity gradient (mean risk): Red 0.3044 · Orange 0.3374 · quiet 0.2575
- _Country-level GDACS vs basin-specific sites -> temporal national check, not per-basin localisation._

### Drift + retraining
- Retrain now: **False** (no drift signal fired)
- _Note: Flood threshold (q95) and severe threshold (q99) derive from TRAIN years only — the test period cannot define its own events._
- _Note: Persistence (today's discharge vs threshold) is the standard operational no-model forecast; beating it is the real bar._

## Fire

- **Source:** USDA FPA-FOD wildfire occurrences + ERA5 fire weather, 12 Pacific-Northwest cells, 2012-2018 daily (real outcomes)
- **Label:** >=1 agency-reported wildfire discovered in the cell on day t+1
- **Split:** temporal: train 2012-2016, test 2017-2018 (incl. the record 2017 season)
- **Train:** 21564 rows (base rate 18.75%) · **Test:** 8724 rows (base rate 19.18%)

**Headline (calibrated, out-of-sample):** AUC 0.8374 · Brier 0.1205 · ECE 0.0227

### vs operational baselines (paired bootstrap on AUC)
| Baseline | Baseline AUC | Model AUC | dAUC [95% CI] | p | significant |
|---|---|---|---|---|---|
| angstrom_index | 0.8220 | 0.8383 | +0.0162 [+0.0124, +0.0194] | 0.0040 | YES |

### At the operating point (threshold chosen on calibration split)
- Target POD 90% -> threshold 0.117
- On test: POD 89.18%, FAR 63.01%, CSI 0.354, bias 2.41 (tp=1492, fp=2542, fn=181)
- Cost model (miss:false-alarm = 100:1): test cost 6609 at the cost-optimal threshold

### Calibration + conformal coverage
- ECE raw 0.1991 -> calibrated 0.0227 (isotonic, fit on calibration split)
- Conformal: target coverage 90%, empirical 89.25%, singleton rate 65.42%

### Blocked generalisation
- Leave-one-region-out: worst AUC 0.8155, mean 0.8427 over 4 regions
- Rolling origin: worst AUC 0.8005, mean 0.836 over 5 years

### Fairness audit (shared threshold)
- **region**: PASS (under-protected: none)
- **state**: PASS (under-protected: none)

### Rare-severe tail
- fire >=100 acres next day: 83 events, POD 96.39% [91.57%, 100.00%]
- fire >=1000 acres next day: 37 events, POD 97.30% [91.89%, 100.00%]

### Lead time vs POD (actionable warning)
- Actionable lead time at POD 80%: **72 h**
| Lead (h) | POD | FAR | AUC | events |
|---|---|---|---|---|
| 24 | 88.76% | 62.78% | 0.8379 | 1673 |
| 48 | 89.90% | 64.79% | 0.8333 | 1673 |
| 72 | 88.94% | 64.34% | 0.8305 | 1673 |

### Degraded-input robustness (fixed model, sensors failing)
- Graceful until POD 70%: **75%** of inputs down
| Inputs lost | POD | FAR | AUC |
|---|---|---|---|
| 0% | 90.02% | 63.55% | 0.8383 |
| 25% | 89.42% | 63.68% | 0.8284 |
| 50% | 85.30% | 64.97% | 0.8053 |
| 75% | 78.42% | 66.50% | 0.774 |

### Drift + retraining
- Retrain now: **False** (no drift signal fired)
- _Note: Study region is OR+WA because that is the public FPA-FOD layer's verified 2012-2018 coverage; provenance is recorded in the fixture._
- _Note: The Angström index baseline is the operational fire-weather formula, computed from the same day's weather as the model's features._

## Fire-India

- **Source:** NASA FIRMS (VIIRS-SNPP) active-fire detections + ERA5 fire weather, 10 Indian fire-belt cells, 2015-2024 daily (~239k in-cell detections, real)
- **Label:** >=1 FIRMS active-fire detection in the cell on day t+1
- **Split:** temporal: train 2015-2021 fire seasons, test 2022-2024 (three held-out seasons, out-of-sample; no cross-year leakage)
- **Train:** 25270 rows (base rate 40.68%) · **Test:** 10930 rows (base rate 42.56%)

**Headline (calibrated, out-of-sample):** AUC 0.8538 · Brier 0.1526 · ECE 0.0151

### vs operational baselines (paired bootstrap on AUC)
| Baseline | Baseline AUC | Model AUC | dAUC [95% CI] | p | significant |
|---|---|---|---|---|---|
| angstrom_index | 0.7965 | 0.8549 | +0.0585 [+0.0520, +0.0646] | 0.0040 | YES |

### At the operating point (threshold chosen on calibration split)
- Target POD 90% -> threshold 0.278
- On test: POD 91.75%, FAR 37.12%, CSI 0.595, bias 1.46 (tp=4268, fp=2519, fn=384)
- Cost model (miss:false-alarm = 100:1): test cost 6235 at the cost-optimal threshold

### Calibration + conformal coverage
- ECE raw 0.0584 -> calibrated 0.0151 (isotonic, fit on calibration split)
- Conformal: target coverage 90%, empirical 90.11%, singleton rate 66.83%

### Blocked generalisation
- Leave-one-region-out: worst AUC 0.7987, mean 0.8594 over 5 regions
- Rolling origin: worst AUC 0.8058, mean 0.8502 over 8 years

### Fairness audit (shared threshold)
- **region**: FLAGGED (under-protected: region:east)
  - remediation (per-group thresholds, fit on calibration): PASS — cost of equity: region:east +14% FAR
- **state**: FLAGGED (under-protected: state:JH)
  - remediation (per-group thresholds, fit on calibration): still flagged: state:CG — cost of equity: state:JH +14% FAR
    - _state:CG: residual threshold gap (consider a lower group threshold / more data)_

### Rare-severe tail
- FRP >=50 next day: 161 events, POD 100.00% [100.00%, 100.00%]
- FRP >=100 next day: 79 events, POD 100.00% [100.00%, 100.00%]

### Lead time vs POD (actionable warning)
- Actionable lead time at POD 80%: **72 h**
| Lead (h) | POD | FAR | AUC | events |
|---|---|---|---|---|
| 24 | 91.32% | 37.02% | 0.8527 | 4652 |
| 48 | 91.12% | 38.01% | 0.8428 | 4649 |
| 72 | 91.30% | 38.84% | 0.8358 | 4646 |

### Degraded-input robustness (fixed model, sensors failing)
- Graceful until POD 70%: **75%** of inputs down
| Inputs lost | POD | FAR | AUC |
|---|---|---|---|
| 0% | 90.00% | 34.89% | 0.8549 |
| 25% | 89.36% | 35.68% | 0.8391 |
| 50% | 88.20% | 38.07% | 0.8154 |
| 75% | 88.03% | 45.79% | 0.7616 |

### Drift + retraining
- Retrain now: **False** (no drift signal fired)
- _Note: Real Indian geography + Feb-May fire season (replaces the Pacific-NW FPA-FOD validation — FIRMS was unreachable when that was first built)._
- _Note: Label is a FIRMS satellite active-fire detection; severity is FRP (fire radiative power), the satellite intensity proxy, not acres._
- _Note: The Angström index baseline is the operational fire-weather formula._
