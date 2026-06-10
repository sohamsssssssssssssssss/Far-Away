# DisasterMind Operations Runbook

Operational reference for the DisasterMind Commander Dashboard service in
production (typically on Railway — see **DEPLOY.md** for the deploy walkthrough).
Everything here is grounded in the running code: the endpoints in
`disastermind/api/app.py` and `disastermind/api/server.py`, the probes in
`disastermind/ops/health.py`, the config in `disastermind/core/config.py`, the
security middleware in `disastermind/security/`, and the `disastermind` CLI.

The service is **stdlib-first and degrades gracefully**: a missing database, a
flaky upstream feed, or an absent broker never takes the process down — it logs
the degradation and falls back to an in-memory path. That design shapes almost
every entry below: most "outages" are *degradations*, and the correct response is
usually to read a probe, not to restart.

---

## 1. Health & readiness probes

There are three HTTP probes. Know which one answers which question.

| Endpoint | Question | Status codes | Who calls it |
| --- | --- | --- | --- |
| `GET /health` | Legacy liveness snapshot (back-compat). | Always `200` if up. | The Railway healthcheck (`railway.json` → `healthcheckPath: /health`); the Dockerfile `HEALTHCHECK` tries `/healthz` then `/health`. |
| `GET /healthz` | **Liveness** — "is the *process* alive?" | Always `200` if the request reaches the handler. | Load balancer / orchestrator liveness probe. A failure here means **restart me**. |
| `GET /readyz` | **Readiness** — "is the *system* ready to coordinate?" | `200` when ready, **`503`** when not. | Load balancer readiness probe. A `503` means **withhold traffic, do NOT restart**. |

### What `/readyz` actually checks

`/readyz` delegates to `disastermind.ops.health.readiness(loop)`. The system is
**ready** only when all three sub-checks pass:

- `agents` — the coordination loop has at least one wired agent;
- `modules` — no modules are degraded (`loop.degraded_modules` is empty);
- `bus` — a live, non-degraded message bus is attached.

A typical not-ready body (HTTP 503):

```json
{
  "status": "not_ready",
  "ready": false,
  "checks": { "agents": "ok", "modules": "fail", "bus": "ok" },
  "detail": { "agent_count": 14, "degraded_modules": ["persistence"], "cycle": 42, "bus": "InMemoryBus" }
}
```

Read `detail.degraded_modules` to see *which* module is degraded — that is your
first lead for any "ready=false" page. A `503` from `/readyz` during a database
outage is **expected and correct** (see §3, "DB unreachable").

> Liveness vs readiness is the load-bearing distinction. Wire the liveness probe
> (`/healthz`) to the *restart* decision and the readiness probe (`/readyz`) to
> the *route-traffic* decision. Never restart on a `/readyz` 503 — you will just
> bounce a process that is healthy but waiting on a degraded backend.

---

## 2. Quick triage

From any shell with `curl` (set `BASE` to your domain; add
`-H "Authorization: Bearer $TOKEN"` to the data routes when auth is on):

```bash
BASE=https://<your-app>.up.railway.app
curl -fsS $BASE/healthz    # liveness  -> always 200 if the box is up
curl -isS $BASE/readyz     # readiness -> 200 ready / 503 not-ready (read the body)
curl -fsS $BASE/metrics    # Prometheus exposition (always a valid document)
curl -fsS $BASE/topics     # {"topic": count, ...} — all-zero => loop not ticking
curl -fsS $BASE/recent     # recent bus messages (newest last)
```

Decision tree:

- `/healthz` fails or times out → process is down/wedged → **restart** (Railway
  restarts `ON_FAILURE`, max 3 retries; check build/boot logs if it keeps dying).
- `/healthz` ok, `/readyz` 503 → a module is degraded → read
  `detail.degraded_modules` → see §3.
- `/healthz` + `/readyz` ok but `/topics` is all zeros → the loop driver is not
  advancing → see §3, "empty dashboard".

---

## 3. Common failure modes & fixes

### Build fails ("Dockerfile does not exist" / wrong build)

**Cause:** Railway's **Root Directory** is not set. The `Dockerfile`,
`railway.json`, and `pyproject.toml` live in the `disastermind/` **subdirectory**,
not at the git root. With Root Directory unset, Railway can't find the Dockerfile
(or falls back to Nixpacks and builds the wrong thing).

**Fix:** Service → **Settings → Source / Build → Root Directory = `disastermind`**.
Confirm Builder = **Dockerfile**. Redeploy. This is the #1 gotcha; see DEPLOY.md §2.

### Empty dashboard (`messages_seen=0`, `/topics` all zeros)

**Cause:** the background **loop driver** is not advancing `loop.run_once`, so
ingestion never ticks. Either `DM_API_DRIVE_LOOP` is disabled, or no loop was
wired.

**Fix:**

1. Ensure `DM_API_DRIVE_LOOP` is **not** set to `0`/`false` (default is on). The
   driver only runs on the serving path (`python -m disastermind.api` /
   `DashboardServer.run`), never inside unit tests.
2. For a livelier stream, set `DM_API_TICK` to a small number of seconds (e.g.
   `2`); otherwise the cadence is `DM_LOOP_INTERVAL` (default `30`s). Give it one
   tick interval, then re-check `/topics`.
3. If `/readyz` shows `agents: fail`, no loop was built — check boot logs for an
   orchestration import error (the server degrades to a stub Commander and serves
   anyway, but with no live activity).

### WebSocket rejected (`/ws` closes immediately)

**Cause (auth on):** browsers cannot set an `Authorization` header on a
`new WebSocket()`. With `DM_API_KEYS` configured, the ASGI security middleware
closes an unauthenticated socket with code **1008** (policy violation).

**Fix:** pass the token in the query string:

```
wss://<your-app>.up.railway.app/ws?token=<your-token>
```

`?api_key=<token>` is accepted as an alias. The HTTP routes still use
`Authorization: Bearer <token>` (or `X-Api-Key`).

**Cause (capacity):** at `DM_WS_MAX` (default `256`) concurrent connections the
server closes new sockets with code **1013** ("try again later").

**Fix:** raise `DM_WS_MAX`, or have clients back off and retry. A client that goes
silent is pruned by the server heartbeat every `DM_WS_PING` seconds (default 20).

### DB unreachable → degraded in-memory

**Cause:** `DM_PERSIST=1` is set but a `DM_*_DSN` backend (Postgres/Timescale) or
`DM_ELASTICSEARCH_URL` is unreachable.

**Behaviour (by design):** each repo **degrades to its own in-memory fallback** —
the box keeps serving, logs the degradation, and `/readyz` reports `modules: fail`
with the degraded module in `detail.degraded_modules`, returning **503** so a load
balancer withholds traffic until the backend recovers. **This is not a crash.**

**Fix:**

1. Verify the DSN env vars resolve (e.g. `${{Postgres.DATABASE_URL}}` references
   the live plugin) and the database is up.
2. While degraded, **state for that repo is volatile** — anything written goes to
   memory and is lost on restart. Avoid relying on durable history until `/readyz`
   is `ready` again.
3. Once the backend is reachable, the next cycle re-binds; `/readyz` returns to
   `200`. No restart is required for recovery, but a restart is harmless.

### 401 on data routes

**Cause:** auth is enabled (`DM_API_KEYS` / `DM_API_KEYS_MAP` set) and the request
carried no/invalid token. **Fix:** send `Authorization: Bearer <token>`. Note that
`/`, `/index.html`, `/health`, `/healthz`, `/readyz`, `/metrics`, `/docs`,
`/openapi.json`, `/redoc` stay **open** even with auth on — if a probe is getting
401, you are hitting the wrong path.

### 429 (rate limited)

**Cause:** a principal exceeded the per-key token bucket. **Fix:** the response
carries a `Retry-After`; back off. Raise `DM_RATE_CAPACITY` (burst) and/or
`DM_RATE_REFILL_PER_SEC` (sustained) if the limit is too tight for legitimate
load. Buckets are keyed by principal name, so an anonymous (auth-off) deployment
shares one `"anonymous"` bucket.

### CORS errors from the operator console

**Cause:** the browser console is on a different origin and `DM_CORS_ORIGINS` does
not include it. **Fix:** set `DM_CORS_ORIGINS` to a comma-separated list of your
console origin(s). Default is `*` (dev-friendly); tighten it in production. Token
auth is header-based (not cookies), so credentialed mode stays off.

---

## 4. Reading `/metrics`

`GET /metrics` renders a Prometheus text exposition (`text/plain; version=0.0.4`)
from the `MetricsCollector` wired onto the bus. It is always a valid document —
even with no collector it returns an empty-but-valid body, so a scrape never
fails. It stays **open without a token** so a Prometheus scraper works behind
auth.

Point Prometheus at `https://<app>/metrics`. Useful signals to alert on:

- per-topic message counters flat-lining → the loop driver stalled (cross-check
  `/topics` and §3 "empty dashboard");
- escalation / dispatch counters → coordination throughput;
- request-duration / error signals from the per-request structured logs (each log
  line carries an `X-Request-ID`, also echoed on every response header for
  correlation).

If `/metrics` is empty when you expect data, observability may have failed to
import (optional dependency) or no collector was wired — the box still serves;
this is a degraded-metrics condition, not an outage.

---

## 5. Scaling

- **Vertical first.** The default in-memory bus and in-memory state are
  **process-local**, so two replicas do **not** share a bus or escalation state —
  each replica runs its own independent loop. Prefer a single, larger instance
  unless you have externalized state.
- **Horizontal** is only coherent when state is externalized: set `DM_PERSIST=1`
  with shared `DM_*_DSN` backends, and use Kafka (`DM_USE_KAFKA=true`,
  `DM_KAFKA_BROKERS`) so replicas share the bus. Until then, treat the service as
  single-writer.
- **Loop cadence vs. load.** A faster `DM_API_TICK` / lower `DM_LOOP_INTERVAL`
  increases CPU and bus volume. Tune `DM_WS_MAX` for the number of live dashboard
  viewers and `DM_RATE_*` for API client load.
- The container runs **non-root** (uid 10001) and binds `$PORT` on `0.0.0.0`; the
  platform router handles TLS termination and sets `X-Forwarded-Proto` (the app
  asserts HSTS only when that indicates HTTPS).

---

## 6. Incident response

1. **Page lands.** Pull up `/healthz`, `/readyz`, `/metrics` (the triage in §2).
2. **Classify:** liveness fail → restart; readiness fail → identify the degraded
   module from `detail.degraded_modules`; both green but stale → loop-driver
   stall (§3).
3. **Correlate** using `X-Request-ID` — it appears on every response header and in
   the structured request log; an inbound `X-Request-ID` is honored so a front
   proxy's id flows through.
4. **Run the self-check** from a shell on the box (or any environment with the
   package):

   ```bash
   python -m disastermind doctor            # Markdown self-check report
   python -m disastermind doctor --json     # machine-readable; exit 1 if anything FAILED
   ```

   `doctor` runs `diagnostics.run_diagnostics` and exits non-zero when any check
   FAILED — handy in an automated runbook.
5. **Inspect the audit trail** for what the system did (and approvals):

   ```bash
   python -m disastermind verify-audit --audit ./audit.jsonl
   ```

   This verifies the hash-chain integrity of the local decision log. Also see the
   `/history/incidents` and `/audit/search?q=&start=&end=` HTTP routes for durable
   history when persistence is on.
6. **Human-in-the-loop actions.** Open escalations are at `GET /escalations`;
   approve/reject via `POST /escalations/{id}/approve` and
   `/escalations/{id}/reject`. Both honor an `Idempotency-Key` header, so a
   retried approval is safe and will not double-dispatch.
7. **Mitigate** per §3, then confirm `/readyz` is `200` and `/topics` is moving
   before closing the incident.

---

## 7. Backup & restore

What is durable depends entirely on whether persistence is on
(`persistence/build.py`):

| State | Where it lives | Durable? |
| --- | --- | --- |
| Resource / spatial state | PostGIS via `DM_POSTGRES_DSN` | Only with `DM_PERSIST=1` and a reachable DB. |
| Telemetry time-series | TimescaleDB via `DM_TIMESCALE_DSN` | Only with `DM_PERSIST=1` and a reachable DB. |
| Audit trail (searchable) | Elasticsearch via `DM_ELASTICSEARCH_URL` | Only when the URL is set and the cluster is up. |
| Audit trail (hash-chained log) | `DM_AUDIT_LOG` JSONL file (default `./audit.jsonl`) | **No** on Railway — the container filesystem is ephemeral. |
| Everything else (bus, escalations) | In-memory | **No** — lost on restart. |

**Backup:**

- Postgres/Timescale: use the platform's managed snapshot/backup for the database
  plugin (e.g. scheduled `pg_dump` or Railway's backup feature). This is the
  source of truth for durable resource state and telemetry.
- Elasticsearch: use cluster snapshots for the audit index.
- The `DM_AUDIT_LOG` JSONL is a **local breadcrumb only** on ephemeral hosts.
  Treat Elasticsearch (`DM_ELASTICSEARCH_URL`) plus the Postgres plugins as the
  durable record; do not depend on the JSONL surviving a redeploy.

**Restore:**

1. Restore the database snapshot(s) into the Postgres/Timescale plugin and the
   Elasticsearch index from its snapshot.
2. Ensure `DM_PERSIST=1` and the `DM_*_DSN` / `DM_ELASTICSEARCH_URL` vars point at
   the restored backends.
3. Redeploy. The repos re-bind on boot; verify with `/readyz` → `200` and confirm
   `/history/incidents` / `/audit/search` return the restored history.
4. Verify audit-chain integrity with `python -m disastermind verify-audit`.

**Disaster recovery sanity:** because the runtime degrades to in-memory, a total
backend loss does **not** take the dashboard down — it runs volatile until the
backends return. Plan capacity accordingly: the priority during a backend outage
is restoring durability, not restarting the (still-serving) app.

---

See **DEPLOY.md** for the full Railway walkthrough, the Root Directory gotcha, and
the complete `DM_*` environment variable table.

---

## 8. Shadow-mode validation (model trust gate)

No hazard-model output may influence a real dispatch until it has run at least
one full season in **shadow mode** — predicting live, acting on nothing, scored
afterwards against what actually happened, and reviewed externally. The harness
is `disastermind.ml.shadow`.

**During the season** (e.g. one monsoon / one fire season):

1. Open one journal per hazard, on durable storage:
   `ShadowJournal("/var/dm/shadow/flood-2026.jsonl")`.
2. At every live forecast, call `record_prediction(...)` with the probability,
   the declared operating threshold, the forecast window and the model version.
   Records are hash-chained; the chain freezes issue order, so a prediction can
   never be quietly rewritten after the outcome is known.
3. When the forecast window closes, attach the real outcome with
   `attach_outcome(...)` (occurred / did not occur, plus the observation
   source in `detail`). Never edit existing lines — outcomes are separate,
   append-only records.

**After the season:**

1. `score_season(journal)` — refuses to run if the hash chain fails
   (`verify_chain()`); produces POD/FAR/AUC/Brier/reliability plus the count
   and ids of unresolved predictions (they are part of the record).
2. `export_for_review(journal)` — one JSON document containing the scorecard
   AND the complete journal. Hand this to the independent review panel; they
   can recompute every number from it.
3. The gate to act on model outputs is: chain verified, season scorecard meets
   the pre-declared POD/FAR targets (see `docs/validation.md` §4), and the
   external review signs off. Anything less stays advisory.

**Retraining while shadowing:** the validation report's `retrain_decision`
(PSI/KS drift + rolling-origin decay) applies unchanged; retraining during a
shadow season is fine — record the new `model_version` on subsequent
predictions so the scorecard can be sliced per version.
