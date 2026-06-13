# DisasterMind — Technical & Product Overview

**An autonomous, multi-agent disaster-coordination platform with validated,
real-data risk prediction and a human-in-the-loop command pipeline.**

DisasterMind ingests live hazard signals for cyclones/floods, earthquakes, and
urban/forest fires; predicts per-asset risk with models validated on real
historical events; models cascading effects; plans resources and evacuation
routes; and drives a human-commanded decision pipeline that ends in dispatch — all
surfaced through a browser command-and-control console.

---

## 1. Executive summary

| | |
|---|---|
| **What it is** | A decision-support system for multi-hazard disaster early warning and coordination. |
| **Hazards** | Cyclone/flood, earthquake (rapid impact assessment), urban/forest fire. |
| **Differentiator** | Every model is validated on **real historical data**, leak-free, and shown to beat the operational baselines with statistical significance — not a demo on synthetic numbers. |
| **Design stance** | Standard-library-first and degrades gracefully; explicitly *decision-support* (a human commander holds authority) with a tamper-evident audit trail. |
| **Scale** | ~37,500 lines of Python across 34 subsystems, a Vite + React 19 + TypeScript console (67 source modules), and a test suite of ~1,030 offline, deterministic tests. |

---

## 2. The problem

Disaster response fails at the seams between sensing, prediction, and action:
warnings arrive too late to act on, forecasts are accurate on average but blind to
the vulnerable, alerts reach phones but not people, and decision-makers are handed
a probability with no notion of what to *do* with it. DisasterMind addresses the
full chain — **sense → predict → decide → disseminate** — and, critically, treats
*validation and honesty* as first-class engineering, so its outputs can earn the
trust an evacuation decision requires.

---

## 3. System architecture

DisasterMind is a **three-tier multi-agent system** communicating over a single
message bus. A hazard signal flows up the tiers and back down to dispatch:

```
 TIER 3 — SENSE            TIER 2 — THINK                          TIER 1 — DECIDE        TIER 3 — ACT
 ──────────────            ──────────────────────────────────     ────────────────       ───────────
 raw_feed         ──►      prediction ─► cascade ─► resource_plan ─► routing_plan ─►
 iot_telemetry                                                       field_order   ─►      commander_review
                                                                                          escalation        ──► dispatch
```

- **Tier 3 (sensing & acting):** ingestion adapters for each hazard, IoT gateways,
  social-signal intake, and outbound dispatch.
- **Tier 2 (analysis & planning):** per-hazard prediction agents, cascading-effect
  models, resource optimisation, evacuation routing, and field tasking.
- **Tier 1 (command):** the commander agent, escalation logic, and concurrent
  multi-incident orchestration.

**Message bus & topics.** All agents publish/subscribe over an in-memory
`MessageBus` on typed topics (`raw_feed`, `iot_telemetry`, `prediction`,
`cascade`, `resource_plan`, `routing_plan`, `field_order`, `commander_review`,
`escalation`, `dispatch`), with five priority levels (CRITICAL→INFO). A
coordination loop ticks on a fixed cadence, builds the agent DAG, and routes every
message through all stages.

**Hazard modules.** Three domains run as independent pipelines: **A — cyclone/
flood**, **B — earthquake**, **C — fire/structural collapse** (plus cross-module
coordination).

**Graceful degradation.** The core runtime is standard-library only. Heavy
capabilities (gradient-boosted models, OR-Tools, Postgres/Timescale, Kafka,
satellite feeds) are optional; when absent, the system falls back to deterministic
in-process implementations rather than failing.

---

## 4. Capabilities in detail

### 4.1 Multi-hazard risk prediction
Per-hazard models score per-asset/per-cell risk from physically meaningful
drivers, each with explainable feature attributions (SHAP-style):
- **Earthquake** — magnitude, depth, location physics → measured damage-grade
  outcome (instrumental intensity / loss alert).
- **Cyclone/flood** — rainfall accumulations, river discharge and trend, surge,
  seasonality → flood-threshold exceedance.
- **Fire** — fire-weather (temperature, humidity, wind), drought/dryness streaks,
  seasonality → next-day ignition.

Models are thin wrappers that prefer a trained gradient-boosted/logistic backend
and fall back to a deterministic in-process model, always returning calibrated
probabilities.

### 4.2 Cascading-effect modeling
Beyond first-order risk, the platform models downstream consequences:
earthquake **aftershock sequences** (Omori-law decay), **flood propagation**
along river networks, and fire spread — so the coordinator anticipates the second
wave, not just the initial event.

### 4.3 Resource allocation & evacuation routing
- **Resource optimiser** allocates scarce assets (teams, vehicles, supplies)
  across competing demands.
- **Road-network routing** plans evacuation routes over real OpenStreetMap road
  graphs to real shelter locations, with road-closure awareness.

### 4.4 Evacuation decision layer
The component that turns a forecast into an actionable order:
- **Clearance-time model** — given zone population, road egress capacity, and
  shelters, computes how long the zone takes to empty and *by when the order must
  be issued*; flags zones that cannot be cleared in the available lead time and
  recommends vertical (in-place) evacuation; supports contraflow and egress
  sensitivity analysis.
- **Vulnerability-weighted phased evacuation** — schedules cohorts (hospitalised,
  elderly/mobility-impaired, transport-dependent, general public) against scarce
  assisted-transport capacity and surfaces exactly which group is left behind when
  warning time runs short.
- **Dissemination & warning-response modeling** — multi-channel reach (cell
  broadcast, SMS, siren, radio, community wardens) combined with a *compliance*
  model that consumes both lead time and false-alarm rate (capturing the
  cry-wolf effect), reporting who is left behind and *why* (not reached vs. won't
  comply vs. cannot be moved).
- **Cost/benefit & break-even** — net lives saved, evacuation-induced casualties,
  and the probability threshold below which ordering an evacuation costs more than
  it saves (over-evacuation guard).
- **Integration capstone** — one per-zone recommendation
  (`ORDER_BY_DEADLINE` / `NOT_CLEARABLE_VERTICAL` / `BELOW_BREAKEVEN_HOLD` /
  `NO_ACTIONABLE_WARNING`) plus a defensible decision record ("what we knew, when,
  and what we recommended").

### 4.5 Human-in-the-loop command & escalation
The commander tier reviews agent recommendations, manages an escalation queue with
approve/reject/timeout semantics, generates escalation memos and situation
briefings, and runs concurrent incidents (one coordination DAG per active
disaster). Every consequential action crosses a defined authority threshold and
requires human approval — mass evacuations (> 10,000 people), cross-jurisdiction
resources, and military assets escalate for sign-off (auto-executing only on
timeout), while declaring a state of emergency, deploying armed forces,
international aid, and critical-infrastructure requisition are *human-only* and
never act without a commander. Only routine field tasking dispatches
unattended. The system never issues a mass-evacuation order autonomously; it recommends,
and a human acts.

### 4.6 Field operations
Device-facing field contracts and a client that close the dispatch → acknowledge →
GPS-update loop, with a durable outbox (terrestrial first, satellite fallback,
offline queue) for connectivity-denied environments.

### 4.7 Emergency alerting
Standards-compliant **CAP 1.2** emergency-broadcast XML generation for
interoperability with public warning systems.

### 4.8 Validation & evidence framework *(the platform's signature capability)*
A complete, dependency-free evaluation suite that scores and documents every
model on real, held-out data:
- **Metrics** — rank-based ROC-AUC, Brier score, accuracy, calibration/ECE.
- **Decision-point metrics** — POD (detection), FAR (false-alarm ratio), CSI,
  frequency bias, HSS at a chosen operating threshold, with explicit
  miss-vs-false-alarm cost accounting.
- **Operational-baseline comparison with significance** — paired-bootstrap tests
  (p-values, confidence intervals) against the incumbents a forecaster uses today.
- **Lead-time-vs-POD curves** — the *actionable warning time*, not just accuracy.
- **Blocked cross-validation** — leave-one-region-out and rolling-origin, reporting
  the worst block, not just the average.
- **Calibrated uncertainty** — isotonic recalibration and split-conformal
  prediction sets with verified coverage.
- **Fairness audit + remediation** — per-subgroup detection rates, with
  equalized-odds remediation that states the false-alarm *cost of equity* and
  classifies residual gaps by cause.
- **Rare-severe-event (tail) analysis** with bootstrap confidence intervals.
- **Drift detection & retraining triggers** — PSI/KS feature drift and a
  skill-decay-driven retrain decision.
- **Degraded-input robustness** — graceful-degradation curves under sensor failure.
- **Shadow mode** — an append-only, hash-chained journal that records live
  predictions before outcomes are known, scores a season against reality, and
  exports a complete record for independent review.

### 4.9 Historical hindcasting & backtesting
- **Named-event replay** — Cyclone **Fani (2019)** and **Amphan (2020)** are
  replayed from their real best-tracks using only pre-cutoff data (leak-free),
  driving the full activation and coordination pipeline and scoring against the
  documented outcome (people evacuated, fatalities, landfall intensity).
- **Population-scale cyclone backtest** — the entire modern North-Indian-Ocean
  record: **92 named, India-landfalling cyclones (1990–2025)** from NOAA IBTrACS,
  scored for activation lead time and landfall-extrapolation error.
- **Full-pipeline backtest** — forecast → evacuation decision → scored against
  reality, on real storms.

### 4.10 Live data ingestion
Adapters target authoritative live sources: **USGS** and **India NCS** seismology,
**Open-Meteo** (GloFAS flood + weather), **NASA FIRMS** active-fire detections,
**India-WRIS** river monitoring, **ISRO Bhuvan** flood inundation, and **IMD**
forecasts. Several operate live and key-free today; the remainder are wired and
require provider keys/endpoint configuration.

### 4.11 Production engineering
Health/readiness probes, retry and circuit-breaker resilience, graceful shutdown;
API authentication, rate limiting, payload validation, CORS hardening; metrics
collection with Prometheus exposition; distributed tracing with per-incident
latency; a tamper-evident audit chain with signing, retention, and backup; durable
storage (Postgres/Timescale, Elasticsearch, MinIO) with versioned migrations;
cross-district mutual-aid federation; a structured-logging stack; and a system
self-check ("doctor") plus a load/throughput benchmark harness.

### 4.12 Web command console (`clients/web`)
A unified Vite + React 19 + TypeScript application (MapLibre GL maps, Recharts) with
four modules — **Commander Dashboard**, **Escalation**, **Field Ops**, and
**Post-Incident Report** — talking to the platform API (configurable base URL),
with live status, override controls, SHAP explanations, and PDF report export.

---

## 5. Validation results (real data, leak-free, out-of-sample)

All figures are produced by the validation suite on committed real-data fixtures
with strictly temporal splits; operating thresholds and calibrators are fit on a
calibration split, never on the test set. See
[`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md) for the full evaluation
including baseline-significance tables, worst-block generalisation, and a frank
failure analysis; reproduce every number with `make reproduce`.

| Hazard | Data source | Out-of-sample AUC | Brier | ECE | Actionable lead (POD ≥ 80%) |
|---|---|---:|---:|---:|---|
| **Earthquake** | USGS catalog (2013–2017, M4.5+) | **0.937** | 0.011 | 0.002 | n/a (instantaneous) |
| **Flood** | GloFAS-ERA5, 12 Indian basins (2010–2023) | **0.944** | 0.028 | 0.004 | **168 h (7 days)** |
| **Fire (PNW)** | USDA FPA-FOD + ERA5 (2012–2018) | **0.837** | 0.121 | 0.023 | **72 h (3 days)** |
| **Fire (India)** | NASA FIRMS VIIRS + ERA5 (2015–2024) | **0.855** | 0.153 | 0.015 | seasonal* |

**Beats the operational incumbents, with statistical significance:**
- **Flood** beats *persistence* (the standard no-model hydrological forecast,
  p ≈ 0.004) and *seasonal climatology* (p ≈ 0.004).
- **Fire** beats the *Angström fire-danger index* — both on US (p ≈ 0.004) and on
  real Indian data (p ≈ 0.02).
- **Earthquake** statistically matches a GMPE ground-motion attenuation baseline on
  the damage label and **beats USGS PAGER by +0.22 AUC** on the felt-report label.

**Population-scale cyclone evidence (92 real storms, IBTrACS):** the system would
have raised a cyclone alert a **median of 54 hours before landfall**, with **≥48 h
lead for ~58%** of storms and ≥72 h for ~40%.

**Operational decision quality** is reported per hazard at the dispatch threshold
(POD/FAR/CSI), with calibration repaired by isotonic recalibration (e.g.,
earthquake ECE 0.21 → 0.002), conformal coverage at target, blocked cross-
validation worst-block scores, a published fairness audit with remediation, and
rare-severe tail analysis.

\* The India fire model is trained on the 2015–2021 FIRMS fire seasons and tested
out-of-sample on three held-out seasons (2022–2024). At the dispatch threshold it
reaches POD 0.92 / FAR 0.37 (CSI 0.60) — the strongest operational decision quality
of the four models. "Seasonal" lead reflects the next-day ignition framing.

---

## 6. Data sources (committed, reproducible)

| Domain | Source | Coverage |
|---|---|---|
| Earthquakes | USGS FDSN catalog | ~36,500 real M4.5+ events, 2013–2017 |
| Floods | GloFAS-ERA5 discharge + ERA5 rainfall (Open-Meteo) | 12 Indian river-basin sites, 2010–2023 |
| Fire (US) | USDA FPA-FOD occurrences + ERA5 | 12 Pacific-Northwest cells, 2012–2018 |
| Fire (India) | NASA FIRMS VIIRS detections + ERA5 | 10 Indian fire-belt cells, 2015–2024 |
| Cyclones | NOAA IBTrACS best tracks | 92 named India-landfalling storms, 1990–2025 |
| Cyclone cases | IBTrACS + documented outcomes (IMD/EM-DAT) | Fani 2019, Amphan 2020 |
| Infrastructure | OpenStreetMap road graph, Copernicus DEM, Census | Puri / Fani impact zone |
| External outcomes | GDACS (UN/EC) declared disasters | 166 Indian flood/cyclone events |

All data is real and citable; every fixture records its provenance. The validation
suite runs fully offline against committed fixtures; a separate fetch utility
rebuilds them from the free public APIs.

---

## 7. Technology & deployment

- **Language/runtime:** Python ≥ 3.11, standard-library-first; optional extras for
  ML (XGBoost/scikit-learn/SHAP), optimisation (OR-Tools/PuLP), geospatial,
  messaging (Kafka), storage (Postgres/Elasticsearch/MinIO), and feeds.
- **API:** framework-free dashboard service over HTTP + WebSocket (health/
  readiness probes, topics, incidents, escalations with approve/reject, live
  stream), OpenAPI-specified.
- **Frontend:** Vite + React 19 + TypeScript, deployable to Vercel.
- **Packaging/CI:** multi-stage Docker image (pinned base, non-root, healthcheck);
  GitHub Actions pipeline with a Python test matrix, lint, type-check, security
  scanning (Trivy + pip-audit), and a CycloneDX SBOM.
- **Deployment targets:** container/Railway and Kubernetes manifests with a SQL
  schema.
- **CLI:** `run`, `simulate`, `train`, `eval`, `doctor`, `serve`, `verify-audit`,
  plus dedicated entry points for validation, hindcasting, diagnostics, and the demo.

---

## 8. Maturity & roadmap

**Current status — a validated research-grade platform.** The prediction and
evaluation layers are rigorous and reproducible; the coordination and evacuation-
decision layers are complete and operate end-to-end on real historical events.

**Known limitations (stated transparently):**
- Earthquakes cannot be *forecast* on an evacuation horizon; the earthquake module
  performs rapid impact assessment, and the evacuation framing applies to
  cyclone/flood/fire.
- Evacuation-planning parameters (clearance times, compliance rates, casualty
  rates) are explicit, tunable planning assumptions pending calibration against
  agency ground truth.
- Some outcome labels are well-justified proxies (discharge exceedance,
  instrumental intensity, satellite detections) rather than surveyed losses.
- Regional generalisation is weaker than the headline: worst-block AUC falls to
  ~0.80 for both fire models (see the technical report's failure analysis).

**Roadmap to operational deployment:**
1. Calibrate the evacuation layer against district-level historical response data.
2. Complete live-feed integration (provider keys + endpoint configuration) and
   expand the India fire record to multiple years.
3. Run a full **shadow-mode season** in partnership with a state disaster-
   management authority — predicting live, acting on nothing, scored against actual
   outcomes — followed by independent review. This is the decisive step toward
   trusted operational use.

---

*DisasterMind — earlier, better-targeted, and more equitable disaster warnings,
built to earn the trust an evacuation decision requires.*
