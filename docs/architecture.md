# DisasterMind — System Architecture

DisasterMind is a multi-agent system that detects a disaster, predicts its
evolution, optimises the response, and drives a **bounded-authority** command
pipeline to dispatch. Routine field tasking dispatches without sign-off; every
consequential action (mass evacuation, cross-jurisdiction resources, military
assets, emergency declarations) escalates to a human commander, and the
highest-consequence actions are human-only. It is India-focused (IMD / CWC /
NCS / ISRO Bhuvan feeds) and spans three hazard modules.

> Sources of truth for this document: `README.md`,
> `disastermind/core/contracts.py`, the per-module `build.py` factories, the
> Tier-1 commander (`disastermind/tier1/commander/`), and the orchestration
> layer (`disastermind/orchestration/`). Endpoint and topic names match the code.

---

## 1. The three-tier authority model

Authority is encoded in `core/contracts.py::Tier` (an `IntEnum` — lower number
means more authority):

| Tier | Enum value | Role | Decision authority |
|------|-----------|------|---------------------|
| **Tier 3 — Edge** | `EDGE = 3` | Ingestion feeds, IoT gateways, social-NLP, dispatch router | **None.** Pure producers/executors. Sense the world and deliver orders; never decide. |
| **Tier 2 — Specialist** | `SPECIALIST = 2` | Prediction, cascade, resource, routing, field coordination | **Autonomous.** Each agent makes and acts on its own decisions (allocate, route, reassign, request reinforcement). It does **not** own the authority matrix. |
| **Tier 1 — Commander** | `COMMANDER = 1` | Commander agent (+ advisory LLM narrator) | **Final review.** Classifies every field order against the Autonomy Threshold Matrix: dispatch autonomously, or escalate to a human. |

A core design rule: **agents never call each other directly.** They communicate
only by publishing/subscribing to named **topics** on a single `MessageBus`
(`core/contracts.py::Topic`). This decoupling is what makes the system degrade
gracefully and lets any agent be swapped or scaled independently.

---

## 2. Hazard modules and activation triggers

Three hazard modules (`core/contracts.py::Module`), each with its own activation
predicate (`orchestration/triggers.py`). A `Signals` snapshot — assembled from
Tier-3 feeds and IoT each cycle — is passed to `should_activate()`, whose
non-`None` result flips `disaster_active`.

| Module | Enum | Hazard | Activates when (`triggers.py`) | Prediction model (`tier2/prediction/agents.py`) |
|--------|------|--------|--------------------------------|--------------------------------------------------|
| **A** | `CYCLONE_FLOOD = "A"` | Cyclone / Flood | IMD cyclonic-storm/deep-depression alert, **or** river gauge ≥ 75% of danger level, **or** dam-discharge ordered, **or** waterlogging breach in ≥ 3 zones (activates ~72 h before projected landfall) | `CyclonePredictionAgent` — XGBoost (tabular) + U-Net CNN (inundation raster) ensemble; deterministic per-100 m-cell inundation heuristic fallback |
| **B** | `EARTHQUAKE = "B"` | Earthquake | Max seismic magnitude ≥ M4.5 (USGS / NCS) — activates within 90 s of detection | `EarthquakeImpactAgent` — HAZUS-style fragility curves per building class (kutcha/pucca/RCC) + Poisson casualty model; ShakeMap-MMI → fragility heuristic fallback |
| **C** | `FIRE_COLLAPSE = "C"` | Urban Fire / Collapse | ≥ 3 brigade calls / zone / 10 min, **or** IoT smoke-heat cluster, **or** NASA FIRMS thermal anomaly, **or** social-NLP collapse cluster (activates immediately on breach) | `FireSpreadAgent` — cellular-automata fire-spread model; deterministic heuristic fallback |

`Module.ALL = "ALL"` is the default scope for cross-cutting messages.

**Activation precedence** (`triggers.py::_PREDICATES`) is ordered by
time-criticality: **earthquake (90 s) → fire (immediate) → flood (72 h lead)**.
`activation_report()` reports *all* concurrently-triggered modules, since
disasters can co-occur.

---

## 3. The single MessageBus and the topic catalogue

All inter-agent traffic is a `core/contracts.py::Message` — a dataclass envelope
that carries the full reasoning chain so the audit trail is complete without
out-of-band lookups. Its fields:

```
sender, recipient, type (MessageType), priority (Priority 1..5),
payload (dict), reasoning (list[str]), ttl_seconds (default 300),
topic (str), incident_id (str|null), module (Module),
escalation_trigger (EscalationTrigger|null), timestamp (ISO-8601 UTC), id (uuid4)
```

`MessageType`: `alert`, `instruction`, `query`, `acknowledgement`, `escalation`.
`Priority`: `CRITICAL=1`, `HIGH=2`, `MEDIUM=3`, `LOW=4`, `INFO=5`.

The well-known topics (`core/contracts.py::Topic`):

| Topic constant | String value | Produced by | Consumed by |
|----------------|--------------|-------------|-------------|
| `RAW_FEED` | `tier3.raw_feed` | Tier-3 ingestion feeds, social-NLP | Tier-2 prediction |
| `IOT_TELEMETRY` | `tier3.iot_telemetry` | Tier-3 IoT gateways (incl. 60 s GPS beacons) | Prediction, field coordinator |
| `PREDICTION` | `tier2.prediction` | Prediction agents | Cascade, resource |
| `CASCADE` | `tier2.cascade` | Cascade agents | Resource, routing |
| `RESOURCE_PLAN` | `tier2.resource_plan` | Resource allocation | Routing, field coordinator |
| `ROUTING_PLAN` | `tier2.routing_plan` | Evacuation routing | Field coordinator |
| `FIELD_ORDER` | `tier2.field_order` | Field coordinator | Commander |
| `COMMANDER_REVIEW` | `tier1.commander_review` | (commander review channel) | — |
| `ESCALATION` | `tier1.escalation` | Commander | LLM narrator, human dashboard |
| `DISPATCH` | `tier3.dispatch` | Commander | Tier-3 dispatch router |

Two package-local topics are declared outside `core/contracts.py` (per the
package-isolation rule, modules never edit core contracts):

- `tier1.escalation_narrative` — the LLM `EscalationNarrator`'s human-readable
  brief (`llm/narrator.py::ESCALATION_NARRATIVE`).
- `api.ws_stream` — a marker owned by the dashboard service
  (`api/service.py::WS_STREAM`); the WebSocket `/ws` endpoint fans out every bus
  message it observes.

---

## 4. Agent roster (wired by `build_system`)

`orchestration/loop.py::build_system` imports each module's `build.py` in a
**subscriber-before-producer** order (all-topic observers first, then reactive
Tier 2/1 + dispatch, then Tier-3 producers last) so subscriptions exist before
any synchronous in-memory fan-out. A module that fails to import or construct is
recorded in `degraded_modules` and skipped; the rest run on.

| Build module | Agent(s) | Tier / role | Subscribes | Publishes |
|--------------|----------|-------------|------------|-----------|
| `observability.build` | `MetricsCollector` | cross-cutting | all topics | (metrics) |
| `persistence.build` | `StatePersistor` | cross-cutting | all topics | (storage) |
| `llm.build` | `EscalationNarrator` (advisory, `decision_authority=False`) | Tier 1 advisory | `tier1.escalation` | `tier1.escalation_narrative` |
| `tier2.prediction.build` | `CyclonePredictionAgent`, `EarthquakeImpactAgent`, `FireSpreadAgent` | Tier 2 | `tier3.raw_feed`, `tier3.iot_telemetry` | `tier2.prediction` |
| `tier2.cascade.build` | `FloodCascadeAgent` (A), `EarthquakeCascadeAgent` (B, Omori-Utsu) | Tier 2 | `tier2.prediction` | `tier2.cascade` |
| `tier2.resource.build` | `ResourceAllocationAgent` (equity LP) | Tier 2 | `tier2.prediction`, `tier2.cascade` | `tier2.resource_plan` |
| `tier2.routing.build` | `EvacuationRoutingAgent` (multi-depot VRP) | Tier 2 | `tier2.cascade`, `tier2.resource_plan` | `tier2.routing_plan` |
| `tier2.field.build` | `FieldCoordinationAgent` | Tier 2 | `tier2.resource_plan`, `tier2.routing_plan`, `tier3.iot_telemetry` | `tier2.field_order` (+ `tier2.resource_plan` for autonomous reinforcement requests) |
| `tier1.commander.build` | `CommanderAgent` (`decision_authority=True`) | Tier 1 | `tier2.field_order` | `tier3.dispatch` or `tier1.escalation` |
| `tier3.dispatch.build` | `DispatchRouter` (owns 5 channels) | Tier 3 | `tier3.dispatch` | (channel sends + receipts) |
| `tier3.iot.build` | `SmokeHeatGateway`, `WaterloggingGateway`, `StructuralGateway`, `GpsBeaconGateway` | Tier 3 | — (producers) | `tier3.iot_telemetry` |
| `tier3.ingestion.build` | `USGSFeedAgent`, `NCSFeedAgent`, `CWCFeedAgent`, `IMDFeedAgent`, `BhuvanFeedAgent`, `OpenMeteoFeedAgent`, `FIRMSFeedAgent`, `OpenWeatherMapFeedAgent` | Tier 3 | — (producers) | `tier3.raw_feed` |
| `tier3.social.build` | `SocialNLPAgent` (Module C feed) | Tier 3 | — (producer) | `tier3.raw_feed` |

The `DispatchRouter` owns five notification channels (`tier3/dispatch/channels.py`),
selected by `payload["channel"]`: `sms`, `push` (FCM), `iridium` (satellite),
`cap` (broadcast), `radio` (field radio). `channel == "all"`/a list fans out to
multiple channels (e.g. mass public warning over SMS + CAP + push). Unknown
channels fall back to SMS. All channels default to dry-run (no network) unless
`DM_DISPATCH_LIVE=1`.

---

## 5. Topic dataflow

```
            ┌──────────────────────────── TIER 3 (edge, no authority) ────────────────────────────┐
            │  ingestion feeds (USGS·NCS·CWC·IMD·Bhuvan·Open-Meteo·FIRMS·OWM) + social-NLP         │
            │  IoT gateways (smoke/heat · waterlogging · structural · GPS beacons @60s)             │
            └───────────────┬─────────────────────────────────────────────┬──────────────────────┘
              tier3.raw_feed │                                             │ tier3.iot_telemetry
                             ▼                                             ▼
            ┌──────────────────────────── TIER 2 (specialist, autonomous) ─────────────────────────┐
            │  prediction  (A cyclone/flood · B quake impact · C fire spread)                       │
            │       │ tier2.prediction                                                              │
            │       ▼                                                                               │
            │  cascade  (flood route cutoff · Omori-Utsu aftershock)                                │
            │       │ tier2.cascade                                                                 │
            │       ▼                                                                               │
            │  resource  (equity-weighted LP allocation)                                            │
            │       │ tier2.resource_plan                                                           │
            │       ▼                                                                               │
            │  routing  (multi-depot VRP, priority-ordered evacuation)                              │
            │       │ tier2.routing_plan                                                            │
            │       ▼                                                                               │
            │  field coordinator  (fuses resource+routing; tracks GPS beacons; autonomous reassign) │
            └───────────────┬───────────────────────────────────────────────────────────────────┘
                            │ tier2.field_order
                            ▼
            ┌──────────────────────────── TIER 1 (commander) ─────────────────────────────────────┐
            │  CommanderAgent — authority-matrix review                                            │
            │     ├─ within autonomous authority ─────────────────► tier3.dispatch                 │
            │     └─ crosses a threshold ─────────────────────────► tier1.escalation               │
            │            (5-min timeout auto-exec UNLESS human-only)        │                       │
            └───────────────┬───────────────────────────────────────────────┬─────────────────────┘
              tier3.dispatch │                                               │ tier1.escalation
                             ▼                                               ▼
            ┌──────────────────────┐                          ┌────────────────────────────────┐
 TIER 3     │ DispatchRouter       │                          │ EscalationNarrator (LLM)         │
            │ sms·push·iridium·    │                          │   → tier1.escalation_narrative   │
            │ cap·radio            │                          │ → human commander dashboard /ws  │
            └──────────────────────┘                          └────────────────────────────────┘
```

A Mermaid version of the same dataflow is in
[`sequence-diagrams.md`](./sequence-diagrams.md#topic-dataflow).

---

## 6. Autonomy and escalation authority model

The Commander classifies **every** field order against the Autonomy Threshold
Matrix (`tier1/commander/matrix.py`). `classify()` is a pure function: given an
order dict (plus an optional `escalation` hint block from the field coordinator)
it returns a `Decision` of one of three kinds.

- **Autonomous** → published to `tier3.dispatch` immediately, no hold. Examples
  (from the PRD): deploy within 50 km, reroute teams, request mutual aid,
  requisition fuel, pre-stage medical/boats. Any order with **no recognised
  escalation trigger** is autonomous.

- **Escalation triggers** (require human approval; auto-execute after a
  **5-minute timeout** — `escalation_timeout_seconds`, default 300 — if no human
  responds): `cross_state_resource_request`, `military_asset_deployment`,
  `mandatory_evacuation_gt_10000`, `requisition_private_infrastructure`,
  `media_broadcast_order`.

- **Human-only** (agent **never** auto-acts, even on timeout — it keeps the
  escalation open until a human decides): `international_aid_request`,
  `declare_state_of_emergency`, `armed_forces_in_civil_situation`,
  `critical_national_infrastructure`. These four are the
  `HUMAN_ONLY_TRIGGERS` frozenset in `core/contracts.py`.

Trigger recognition has two independent sources so it works regardless of how
the upstream field agent populated the payload: (1) an explicit `escalation`
block on the `FIELD_ORDER` payload, else (2) heuristic inference from the order
contents (keywords in `reason`, and a mass-evacuation `scale` threshold of
10 000). Human-only keywords are checked first so the most restrictive
classification wins.

Timeout handling is **event-driven, never blocking**: `CommanderAgent.tick()`
(driven by the coordination loop with the loop clock) calls
`resolve_pending(now_epoch)` each cycle. For each due escalation: human-only →
keep waiting and emit nothing; otherwise → auto-execute (publish `tier3.dispatch`
with `via="auto_execute_on_timeout"`). Humans resolve a pending escalation
out-of-band via `approve()` / `reject()`, wired to the dashboard's POST
endpoints.

A pending escalation's dashboard shape (`CommanderAgent.pending_reports()`):
`{report_id, trigger|null, human_only, deadline_epoch, status, incident_id}`
where `status ∈ {pending, approved, rejected, auto_executed, expired}`.

The advisory **`EscalationNarrator`** (`llm/narrator.py`) subscribes to
`tier1.escalation` and emits a five-section human brief (Situation Summary / Why
This Exceeded Autonomous Authority / Recommended Action / Key Risks / Decision
Deadline) on `tier1.escalation_narrative`. It uses Claude (`claude-opus-4-8`)
when an API key is configured, otherwise a deterministic template. It has
`decision_authority = False` and never dispatches or mutates the escalation.

---

## 7. Equity and priority ordering

**Resource allocation** (`tier2/resource/`) maximises *equity-weighted* population
covered. `VulnerabilityProfile.weight()` folds elderly density, hospital
proximity, road accessibility and informal-settlement density into a single
multiplier weighted **equally** with urban centres, so a vulnerable cell of N
people is treated like an urban cell of `N × weight` people. Cascade urgency
(road/bridge failure windows from `tier2.cascade`) further raises priority. The
optimiser uses a Mixed-Integer LP via **PuLP** when available and degrades
silently to a deterministic weighted-greedy assignment in pure stdlib.

**Evacuation routing** (`tier2/routing/`) solves a multi-depot, capacity-aware,
priority-weighted Vehicle Routing Problem (OR-Tools when available, else a
nearest-neighbour-insertion stdlib fallback). It serves population classes in a
fixed order: **mobility-impaired → elderly → children → hospitalised →
general**.

---

## 8. Audit and explainability

Every message is logged through a **tamper-evident SHA-256 hash chain**
(`audit/decision_log.py::DecisionLogger`); each record's hash chains off the
previous one, so `verify_chain()` detects any retroactive edit. Each ML
prediction logs SHAP-style feature attributions alongside its risk payload.
Storage is durable JSONL locally (default `./audit.jsonl`); optional
Elasticsearch / TimescaleDB / PostGIS / MinIO backends are available via
`docker-compose.yml`. The CLI `verify-audit <path>` re-walks the chain and
reports OK or TAMPERED.

---

## 9. Graceful degradation

DisasterMind's core runs on the Python standard library with deterministic
heuristic fallbacks; optional engines are auto-detected and swapped in when
installed.

- **Bus failover:** `KafkaBus` fails over from primary brokers to backup
  brokers, then to the in-memory bus.
- **Module isolation at boot:** a module that won't import/construct is skipped
  by `build_system` and reported in `degraded_modules`; the rest run on.
- **Agent isolation at runtime:** `run_once()` wraps each agent's tick in a
  try/except, so one failing agent does not stop the cycle. The commander's
  timeout sweep is likewise guarded.
- **Heavy libs absent:** prediction (xgboost/sklearn/shap), optimisation
  (pulp/ortools), bus (confluent-kafka) and storage clients are all lazy; when
  absent, deterministic stdlib heuristics keep every agent live.
- **Dispatch channels** default to dry-run (no network) unless
  `DM_DISPATCH_LIVE=1`.

---

## 10. Operator surfaces

- **CLI** (`disastermind/cli.py`): `run`, `simulate {A|B|C} [--escalate]`,
  `verify-audit <path>`. See [`runbook.md`](./runbook.md).
- **Commander dashboard API** (`disastermind/api/`): a framework-free
  `DashboardService` (topic counts, incidents, escalation approve/reject) plus a
  thin FastAPI/WebSocket transport. The HTTP/WS contract is specified in
  [`openapi.yaml`](./openapi.yaml).
- **Web console** — the unified browser UI lives under
  [`clients/web/`](../clients/web/) (Vite + React): Commander Dashboard,
  Escalation, Field Ops and Post-Incident Report modules, talking to the
  dashboard API. A single-file vanilla-JS dashboard also ships at
  `disastermind/api/static/index.html` (served at `/`). The web console's
  **Field Ops** module replaces the former standalone field client; the
  field-device wire contracts remain in `disastermind/fieldapp/contracts.py`.
