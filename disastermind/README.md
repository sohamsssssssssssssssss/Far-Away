# DisasterMind — Autonomous Disaster Coordination (Group A)

A multi-agent system that detects a disaster, predicts its evolution, optimises
the response, and issues field & public orders **autonomously**, escalating to
humans only when a decision crosses a defined authority threshold. India-focused
(IMD / CWC / NCS / ISRO Bhuvan feeds) across three hazard modules:

| Module | Hazard | Activates |
|--------|--------|-----------|
| **A** | Cyclone / Flood | 72 h before projected landfall (IMD alert, gauge ≥ 75% danger, dam discharge, or waterlogging in 3+ zones) |
| **B** | Earthquake | within 90 s of an M4.5+ detection (USGS / NCS) |
| **C** | Urban Fire / Collapse | immediately on threshold breach (3+ brigade calls/zone/10 min, IoT smoke-heat cluster, FIRMS anomaly, or social-NLP collapse cluster) |

## Architecture — three tiers, one message bus

```
                         ┌──────────────────────────────────────────────┐
 TIER 3 (edge,           │  ingestion   IMD·CWC·USGS·FIRMS·ISRO·          │
 no decision authority)  │  agents      Open-Meteo·OWM·NCS                │
                         │  iot         smoke/heat · waterlogging ·        │
                         │  gateways    structural · GPS beacons           │
                         └───────┬───────────────────────┬────────────────┘
                       RAW_FEED  │                        │ IOT_TELEMETRY
                                 ▼                        ▼
                         ┌──────────────────────────────────────────────┐
 TIER 2 (specialist,     │  prediction  A cyclone/flood (XGBoost+U-Net)   │
 autonomous decisions)   │              B quake impact (HAZUS+Poisson)     │
                         │              C fire spread (cellular automata)  │
                         │                  │ PREDICTION                   │
                         │                  ▼                              │
                         │  cascade     flood-cascade · Omori-Utsu         │
                         │              aftershock · hazmat                 │
                         │                  │ CASCADE                      │
                         │                  ▼                              │
                         │  resource    LP allocation + equity constraint  │
                         │                  │ RESOURCE_PLAN                │
                         │                  ▼                              │
                         │  routing     multi-depot VRP, priority order    │
                         │                  │ ROUTING_PLAN                 │
                         │                  ▼                              │
                         │  field       team tracking + reassignment       │
                         └──────────────────┬───────────────────────────┘
                                            │ FIELD_ORDER
                                            ▼
                         ┌──────────────────────────────────────────────┐
 TIER 1 (commander)      │  commander   authority-matrix review           │
                         │              ├─ autonomous → DISPATCH           │
                         │              └─ escalate   → ESCALATION         │
                         │                 (5-min timeout auto-exec        │
                         │                  unless human-only)             │
                         └───────┬──────────────────────┬─────────────────┘
                        DISPATCH │                       │ ESCALATION
                                 ▼                       ▼
                         ┌───────────────────┐   ┌──────────────────┐
 TIER 3 (edge)           │ dispatch router   │   │ human commander  │
                         │ SMS·FCM·Iridium·  │   │ dashboard        │
                         │ CAP·radio         │   └──────────────────┘
                         └───────────────────┘
```

Agents never call each other directly — they communicate only via **topics** on a
`MessageBus` (see `core/contracts.py::Topic`). This is what makes the system
degrade gracefully and lets any agent be swapped or scaled independently.

## Quickstart

```bash
# stdlib-only: no broker, solver, ML lib or network required
python -m pytest -q                      # 620 tests, all offline (stdlib only)

python - <<'PY'                          # drive a synthetic disaster
from disastermind.orchestration.build import build_system, should_activate, Signals
print(should_activate(Signals(max_seismic_magnitude=6.2)))   # -> Module.EARTHQUAKE
loop = build_system()                    # wires all 19 agents on one in-memory bus
loop.run_once()                          # one 30s-equivalent cycle (no sleep)
print({t: sum(m.topic==t for m in loop.bus.history) for t in {m.topic for m in loop.bus.history}})
PY
```

Run the real wall-clock loop: `loop.run(max_cycles=N)` ticks every
`DM_LOOP_INTERVAL` (default 30 s) while `disaster_active`.

### Optional capabilities (graceful upgrades)

The core runs on the Python standard library with deterministic heuristic
fallbacks. Install extras to swap in the real engines — code auto-detects them:

```bash
pip install -e '.[ml]'        # xgboost, sklearn, shap   (prediction)
pip install -e '.[optimise]'  # pulp, ortools            (resource LP, routing VRP)
pip install -e '.[bus]'       # confluent-kafka          (KafkaBus)
pip install -e '.[storage]'   # psycopg, elasticsearch, minio
pip install -e '.[all]'       # everything
```

## Autonomy & escalation model (Step 7)

The Commander classifies every field order against an authority matrix:

* **Autonomous** → dispatched immediately (deploy within 50 km, reroute teams,
  request mutual aid, requisition fuel, pre-stage medical/boats).
* **Escalation triggers** (human approval, 5-min timeout then auto-execute):
  cross-state resource, military asset, mandatory evacuation > 10 000,
  requisition private infrastructure, media broadcast.
* **Human-only** (agent *never* auto-acts, even on timeout): international aid,
  declaring a state of emergency, armed forces in civil situations, critical
  national infrastructure.

## Equity & priority (Steps 4–5)

Resource allocation weights elderly density, hospital proximity, road
accessibility and informal-settlement density **equally** with urban centres
(`VulnerabilityProfile.weight()`). Evacuation routing serves, in order:
mobility-impaired → elderly → children → hospitalised → general.

## Audit & explainability (Step 9)

Every message is logged through a **tamper-evident SHA-256 hash chain**
(`audit/decision_log.py`); `verify_chain()` detects any retroactive edit. Each ML
prediction logs SHAP-style feature attributions. Durable JSONL locally; optional
Elasticsearch / TimescaleDB / PostGIS / MinIO via `docker-compose.yml`.

## Graceful degradation (Step 10)

* Kafka down → `KafkaBus` fails over to backup brokers, then to in-memory.
* A module that won't import is skipped at boot (`build_system` reports
  `degraded_modules`); the rest run on.
* Heavy libs absent → deterministic stdlib heuristics keep every agent live.

## CLI

```bash
python -m disastermind run --max-cycles 10           # drive the live loop
python -m disastermind simulate A|B|C [--escalate]   # inject a synthetic scenario
python -m disastermind train --out models/           # train per-module ML artifacts
python -m disastermind eval                          # backtest models (AUC/Brier/ECE) + model cards
python -m disastermind doctor                         # system self-check (DAG balance, config, audit)
python -m disastermind serve                          # run the dashboard API (uvicorn)
python -m disastermind verify-audit audit.jsonl      # check the hash-chain
python -m disastermind.demo B                         # narrated end-to-end demo
```

## Extended surface

Built on top of the Group A core (all optional/heavy deps lazy with fallbacks):

* **`llm/`** — Group B escalation layer: `EscalationNarrator` consumes `ESCALATION`
  and emits a human-readable brief on `tier1.escalation_narrative`. Uses Claude
  (`claude-opus-4-8`) when an API key is set, else a deterministic template — wired
  into `build_system`.
* **`storage/`** — `PostgisResourceRepo`, `TimescaleTelemetryRepo`,
  `ElasticsearchAuditRepo`, `MinioArtifactStore` + a `Storage` facade (offline by
  default; `Storage.from_settings(live=True)` for real backends).
* **`api/`** — commander dashboard: framework-free `DashboardService` (topic counts,
  incidents, escalation approve/reject) + a thin FastAPI/WebSocket transport.
* **`runtime/`** — `KafkaConsumerRuntime` + `ProcessRunner` (SIGINT graceful stop).
* **`observability/`** — all-topic `MetricsCollector` + Prometheus exposition +
  `health(loop)` — wired into `build_system`.
* **`scenarios/`** — synthetic A/B/C generators behind the CLI `simulate`.

## Layout

```
disastermind/
  core/        contracts (Message/Topic/enums), bus, BaseAgent, config
  models/      geo primitives + domain dataclasses
  audit/       hash-chained DecisionLogger
  tier3/       ingestion (+ live fetch) · iot · dispatch   (no decision authority)
  tier2/       prediction (+ ml seam) · cascade · resource · routing · field
  tier1/       commander  (authority matrix + escalation)
  orchestration/ triggers (Step 1) + coordination loop (Step 10)
  llm/         Group B escalation narrator + decision-support advisor
  storage/     PostGIS · TimescaleDB · Elasticsearch · MinIO repos + facade
  integrations/ real Kafka round-trip, PostGIS/Timescale SQL + DDL, ES query DSL, health
  api/         DashboardService + FastAPI/WebSocket dashboard + uvicorn server
  runtime/     Kafka consumer + process runner
  live/        LiveSystem (real backends) + live feed ingest + resilient polling
  observability/ metrics collector + Prometheus exposition + health
  tracing/     span recorder + all-topic trace collector (per-incident latency)
  security/    opt-in API auth + rate limiting + payload validation
  ml/          XGBoost/sklearn risk models + SHAP; training/ + eval/ (backtest, model cards)
  multi_incident/ IncidentManager — concurrent incidents, one DAG per disaster
  ops/         health/readiness, retry, circuit breaker, graceful shutdown, config check
  alerting/    CAP 1.2 emergency-broadcast XML
  fieldapp/    device contracts + MockFieldClient (closes dispatch→ACK→GPS loop)
  benchmarks/  load/throughput harness — python -m disastermind.benchmarks
  diagnostics/ system "doctor" — python -m disastermind.diagnostics
  demo/        narrated end-to-end runner — python -m disastermind.demo
  scenarios/   synthetic A/B/C scenario generators
  cli.py       python -m disastermind {run,simulate,train,eval,doctor,serve,verify-audit}
deploy/        k8s manifests + sql/schema.sql; Dockerfile, Makefile, CI
tests/         unit + e2e + scenario + perf + integration (620 tests; integration gated by DM_INTEGRATION)
```
