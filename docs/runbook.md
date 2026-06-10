# DisasterMind — Operator Runbook

Day-to-day operation of the DisasterMind coordination platform: install, bring up
backends, drive the loop, serve the dashboard, and handle escalations. Every
command below is grounded in `disastermind/cli.py`, `api/`, and `docker-compose.yml`.

> The interpreter may be `python` or `python3` on your host — use whichever
> resolves. The core requires **no** optional dependency and **no** network.

---

## 1. Install

```bash
# Core (stdlib-only) + tests:
pip install -e '.[dev]'

# Optional capability extras — code auto-detects and swaps the real engine in:
pip install -e '.[ml]'        # xgboost, scikit-learn, shap        (prediction)
pip install -e '.[optimise]'  # pulp, ortools                      (resource LP, routing VRP)
pip install -e '.[bus]'       # confluent-kafka                    (KafkaBus)
pip install -e '.[storage]'   # psycopg, elasticsearch, minio      (durable backends)
pip install -e '.[all]'       # everything, incl. FastAPI/uvicorn for the dashboard
```

Smoke-test (offline, no broker/solver/ML/network needed):

```bash
python -m pytest -q          # full unit + e2e suite
```

---

## 2. Bring up the backing services (optional)

The app runs fully without these (in-memory bus + JSONL audit). Bring them up to
exercise the production storage/bus paths (PRD Step 9 storage, Step 10 bus).

```bash
docker compose up -d
```

| Service | Port(s) | Role |
|---------|---------|------|
| `kafka` / `kafka-backup` | 9092 / 9094 | message bus (primary + failover, Step 10) |
| `postgis` | 5432 | spatial asset/zone state (PostgreSQL + PostGIS) |
| `timescaledb` | 5433 | sensor telemetry time-series |
| `elasticsearch` | 9200 | decision-audit index / search |
| `minio` | 9000 / 9001 | object storage (imagery, model artefacts) + console |

Tear down: `docker compose down` (add `-v` to drop volumes).

---

## 3. Drive the coordination loop

```bash
python -m disastermind run --max-cycles 10
# --audit <path>   JSONL decision log (default: settings.audit_log_path)
# --no-audit       in-memory null logger (no disk writes)
```

Prints cycles executed, per-topic message counts, and DISPATCH/ESCALATION totals.
A degraded boot (a module that failed to import) is reported on stderr but the
rest of the system runs on.

The real wall-clock loop ticks every `DM_LOOP_INTERVAL` (default 30 s) while
`disaster_active`; `run` uses a deterministic non-blocking clock so it terminates
after `--max-cycles`.

---

## 4. Inject a synthetic scenario

```bash
python -m disastermind simulate A           # cyclone / flood
python -m disastermind simulate B           # earthquake
python -m disastermind simulate C           # urban fire / collapse
python -m disastermind simulate B --escalate # force a human-escalation path (Step 7)
# --limit N   max DISPATCH/ESCALATION lines printed (default 5)
```

Use this to populate the dashboard while developing the operator console.

---

## 5. Serve the Commander dashboard

```bash
python -m disastermind serve --host 127.0.0.1 --port 8000
#   (requires the '[all]' extra for uvicorn/FastAPI; lazily imported)
# equivalent: uvicorn disastermind.api.app:create_app --factory --port 8000
```

Endpoints (see [`openapi.yaml`](./openapi.yaml)): `GET /health`, `GET /topics`,
`GET /incidents`, `GET /escalations`, `POST /escalations/{id}/approve|reject`,
`WS /ws`. A single-file reference UI is served at `/`
(`disastermind/api/static/index.html`).

**Web console** — the unified React/Vite UI (Commander Dashboard, Escalation,
Field Ops, Post-Incident Report — map + charts + escalation queue):

```bash
cd clients/web && npm install && npm run dev   # http://localhost:5173
```

Set `VITE_API_BASE_URL` (in `clients/web/.env`) to the backend API, e.g.
`http://localhost:8000`; it defaults to the deployed instance. See
`clients/web/README.md`.

---

## 6. Handle escalations (PRD Step 7)

The Commander classifies every field order against the authority matrix:

- **Autonomous** → dispatched immediately, no human action.
- **Escalation triggers** (`cross_state_resource_request`,
  `military_asset_deployment`, `mandatory_evacuation_gt_10000`,
  `requisition_private_infrastructure`, `media_broadcast_order`) → appear under
  `GET /escalations`. **Approve** dispatches now; **Reject** emits a rejection
  ACK. If no human responds within **5 minutes** (`deadline_epoch`), the order
  **auto-executes**.
- **Human-only** (`international_aid_request`, `declare_state_of_emergency`,
  `armed_forces_in_civil_situation`, `critical_national_infrastructure`) → the
  agent **never** auto-executes; the escalation stays open until a human decides.
  These rows carry `human_only: true`.

From the console: pick an escalation, confirm the approver name, click
Approve/Reject. From the API directly:

```bash
curl -X POST "http://localhost:8000/escalations/<report_id>/approve?approver=cmdr"
curl -X POST "http://localhost:8000/escalations/<report_id>/reject?approver=cmdr&note=stand+down"
```

The advisory `EscalationNarrator` posts a five-section human brief on
`tier1.escalation_narrative` (Claude when `ANTHROPIC_API_KEY` is set, else a
deterministic template).

---

## 7. Audit & verify (PRD Step 9)

```bash
python -m disastermind verify-audit audit.jsonl
#   -> "audit chain OK: N record(s) verified, hash-chain intact"
#   -> "audit chain TAMPERED: ..."  (non-zero exit) if any record was edited
```

Every message is logged through a tamper-evident SHA-256 hash chain; each ML
prediction logs SHAP-style attributions alongside its payload.

---

## 8. Health & diagnostics

```bash
python -m disastermind doctor                 # Markdown self-check report
python -m disastermind doctor --json          # machine-readable
python -m disastermind doctor --audit audit.jsonl   # also verify the chain
curl http://localhost:8000/health             # live dashboard liveness snapshot
```

---

## 9. Graceful degradation — what you'll see

| Failure | Behaviour |
|---------|-----------|
| Kafka primary down | `KafkaBus` fails over to backup brokers, then to in-memory; logs a degradation event. |
| A module won't import | Skipped at boot; reported in `degraded_modules` / on stderr; the rest run on. |
| An agent raises mid-cycle | `run_once()` isolates it; the cycle continues. |
| Heavy libs absent (ml/optimise/bus/storage) | Deterministic stdlib heuristics keep every agent live. |
| Full platform down | Field teams keep executing last received orders; the field app works offline with Iridium fallback. |

Dispatch channels are **dry-run by default**; set `DM_DISPATCH_LIVE=1` to send for real.

---

## 10. Troubleshooting

- **`serve` says it can't start / FastAPI missing** → `pip install -e '.[all]'`.
- **Dashboard panels empty** → no traffic yet; run `simulate` or `run` against the
  same process/bus, or confirm the console proxy target (`:8000`).
- **`/escalations` always empty** → escalations only appear for threshold-crossing
  orders; try `simulate B --escalate`.
- **Integration tests all skip** → backends aren't up or client libs aren't
  installed; `docker compose up -d` then `DM_INTEGRATION=1 pytest tests/integration -q`
  (see `tests/integration/README.md`).
- **Audit "TAMPERED"** → the JSONL log was edited after the fact; investigate the
  break point reported by `verify-audit`.
