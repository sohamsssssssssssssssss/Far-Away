# Deploying DisasterMind on Railway

This is a step-by-step walkthrough for deploying the DisasterMind Commander
Dashboard API to [Railway](https://railway.app). It is grounded in the actual
code: the container `CMD` (`python -m disastermind.api`), `railway.json`, the
`Dockerfile`, and the environment flags read by `core/config.py`,
`api/server.py`, `security/auth.py`, `security/ratelimit.py`, and
`persistence/build.py`.

The default deployment is **fully offline / stdlib-first**: it builds the agent
DAG, serves the dashboard, and drives a synthetic incident stream. No database,
no message broker, and no external feed credentials are required to get a green
deploy. You turn on durability, live feeds, and auth incrementally with `DM_*`
environment variables — every one of them is OFF by default.

---

## 0. Repository layout (read this first)

The git repository root is the **parent** of the Python project. The project —
the `pyproject.toml`, the `Dockerfile`, `railway.json`, and the `disastermind/`
package — lives in a **subdirectory** called `disastermind/`:

```
<git root>/
└── disastermind/            <-- set Railway "Root Directory" to THIS
    ├── Dockerfile           <-- builder Railway must find
    ├── railway.json
    ├── pyproject.toml
    └── disastermind/        <-- the Python package
        ├── api/             (DashboardServer, create_server, app.create_app)
        ├── core/config.py   (Settings — the DM_* env table)
        ├── security/        (auth.py, ratelimit.py)
        └── persistence/     (build.py — DM_PERSIST)
```

> ### THE #1 GOTCHA — Root Directory
> Railway's Dockerfile builder looks for `Dockerfile` at the **service root**.
> Because the `Dockerfile` is in the `disastermind/` **subdirectory**, you MUST
> set the service's **Root Directory** to `disastermind`. If you skip this the
> build fails with "Dockerfile does not exist" (or Railway falls back to
> Nixpacks and builds the wrong thing). See the RUNBOOK "build fails" entry.

---

## 1. Create the service from GitHub

1. In the Railway dashboard, **New Project → Deploy from GitHub repo**.
2. Authorize Railway and pick the repository that contains DisasterMind.
3. Railway creates a service and attempts a first build. It will likely fail or
   build the wrong directory until you complete step 2 — that is expected.

## 2. Set the Root Directory (the #1 gotcha)

1. Open the service → **Settings → Source / Build**.
2. Set **Root Directory** to `disastermind`.
3. Confirm the **Builder** is **Dockerfile** (this is what `railway.json`
   declares: `"builder": "DOCKERFILE"`, `"dockerfilePath": "Dockerfile"`).

With Root Directory set, Railway uses the project's `railway.json`:

```json
{
  "build":  { "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
  "deploy": {
    "startCommand": "python -m disastermind.api",
    "healthcheckPath": "/health",
    "healthcheckTimeout": 100,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3
  }
}
```

The start command is `python -m disastermind.api`. That entrypoint reads `$PORT`
(injected by Railway) and binds `0.0.0.0`, starts the background coordination
loop driver, then serves the FastAPI app + static dashboard with uvicorn.

## 3. Port binding (automatic)

Do **not** hard-code a port. `python -m disastermind.api` chooses the bind host
and port like this:

| Setting | Source | Behaviour |
| --- | --- | --- |
| Host | `DM_API_HOST`, else `0.0.0.0` when `$PORT` is set, else `127.0.0.1` | On Railway `$PORT` is set, so it binds `0.0.0.0`. |
| Port | `$PORT`, else `DM_API_PORT`, else `8000` | Railway injects `$PORT`; just leave it. |

The Dockerfile's `HEALTHCHECK` also tracks `$PORT` and accepts `/healthz` or
`/health`.

## 4. First green deploy (no env vars needed)

Deploy. The healthcheck path `/health` returns 200 as soon as the dashboard is
up. Once green, click **Settings → Networking → Generate Domain** to get a
public `*.up.railway.app` URL. Open it in a browser — you should see the
single-file Commander Dashboard. It polls `GET /topics` / `GET /escalations` and
streams the bus over the `/ws` WebSocket.

If the dashboard loads but stays empty (`messages_seen=0`), see step 6
(`DM_API_DRIVE_LOOP` / `DM_API_TICK`) and the RUNBOOK.

---

## 5. The DM_* environment variable table

Set these under the service's **Variables** tab. Every variable is OFF / safe by
default; set only what you need. Boolean flags accept `1/true/yes/on` (anything
else, including unset, is false).

### Coordination loop — `core/config.py` (`Settings`)

| Variable | Default | Effect |
| --- | --- | --- |
| `DM_LOOP_INTERVAL` | `30` | Seconds between coordination cycles. |
| `DM_ESCALATION_TIMEOUT` | `300` | Commander escalation auto-execute timeout (s). |
| `DM_GRID_METERS` | `100` | Risk-grid cell size (metres). |

### Dashboard API / bind — `api/__main__.py`

| Variable | Default | Effect |
| --- | --- | --- |
| `PORT` | `8000` | Injected by Railway. Bind port; do not set manually. |
| `DM_API_HOST` | `0.0.0.0` (hosted) | Bind address override. |
| `DM_API_PORT` | `8000` | Bind port when `$PORT` is absent. |

### Live loop driver — `api/server.py`

| Variable | Default | Effect |
| --- | --- | --- |
| `DM_API_DRIVE_LOOP` | `true` | Background thread that calls `loop.run_once` so the dashboard shows live activity. Set `0`/`false` to serve a static, non-advancing dashboard. |
| `DM_API_TICK` | (unset → `DM_LOOP_INTERVAL`) | Override driver cadence in seconds (fractional allowed, e.g. `2`). Useful for a livelier demo. |

### Security / auth — `security/auth.py` + `security/ratelimit.py`

Auth is **off by default** (the deployment is fully open). It is enforced ONLY
when at least one token is configured. Setting any of `DM_API_KEYS` /
`DM_API_KEYS_MAP` turns on the ASGI security middleware (bearer-token auth +
per-principal rate limiting). `/`, `/index.html`, `/health`, `/healthz`,
`/readyz`, `/metrics`, `/docs`, `/openapi.json`, `/redoc` stay open so probes and
scrapers keep working behind auth.

| Variable | Default | Effect |
| --- | --- | --- |
| `DM_API_KEYS` | (empty → auth OFF) | Comma/whitespace-separated bearer tokens. Each bare token gets a fingerprint principal for audit attribution. |
| `DM_API_KEYS_MAP` | (empty) | Named `principal:token` pairs, comma-separated (e.g. `commander:abc123,observer:def456`). |
| `DM_CORS_ORIGINS` | `*` | Comma-separated allowed browser origins for the operator console. Token auth is header-based, so credentialed mode is off and `*` is allowed. |
| `DM_RATE_CAPACITY` | `60` | Token-bucket burst capacity per principal. |
| `DM_RATE_REFILL_PER_SEC` | `60.0` (`.env.example` suggests `10`) | Sustained refill rate (tokens/second). |

### Persistence / durable state — `persistence/build.py` + `core/config.py`

State is in-memory by default (survives nothing across restarts). Set
`DM_PERSIST=1` (alias `DM_LIVE=1`) to build durable storage; each repo still
degrades to its own in-memory fallback if its backend is unreachable, so this
never hard-fails the boot.

| Variable | Default | Effect |
| --- | --- | --- |
| `DM_PERSIST` | `false` | Turn on durable backends. Alias: `DM_LIVE`. |
| `DM_POSTGRES_DSN` | `postgresql://localhost/disastermind` | PostGIS resource/spatial state. |
| `DM_TIMESCALE_DSN` | `postgresql://localhost/dm_telemetry` | TimescaleDB sensor time-series. |
| `DM_ELASTICSEARCH_URL` | (empty → JSONL audit only) | Elasticsearch audit trail. |
| `DM_AUDIT_LOG` | `./audit.jsonl` | Local hash-chained audit log path. |

### Live feeds — `api/server.py` + `tier3/ingestion/build.py`

| Variable | Default | Effect |
| --- | --- | --- |
| `DM_FEEDS_LIVE` | `false` | Poll REAL upstream feeds (USGS quakes, Open-Meteo) each cycle through the resilient poller (per-feed circuit breaker) instead of the synthetic sample stream. |
| `DM_USGS_URL` | USGS all-hour GeoJSON | USGS earthquake feed URL. |
| `DM_OPENMETEO_URL` | Open-Meteo forecast | Weather feed URL. |
| `DM_IMD_URL` | `https://mausam.imd.gov.in` | IMD base URL. |
| `DM_FIRMS_KEY` | (empty → offline samples) | NASA FIRMS wildfire API key. |

### Transport hardening — `api/app.py`

These tune the request-body ceiling and the `/ws` WebSocket heartbeat / connection
cap. Each falls back to a safe built-in default (shown below) when unset; raise or
lower them only if you have a reason to.

| Variable | Default | Effect |
| --- | --- | --- |
| `DM_MAX_BODY` | `1048576` (1 MiB) | Max accepted request-body size in bytes. The dashboard's POSTs carry only query params, so the default is generous; it guards against oversize/hostile bodies. |
| `DM_WS_PING` | `20` | Seconds between server-side `/ws` heartbeat pings; a dead/half-open client is pruned when a ping fails. |
| `DM_WS_MAX` | `256` | Cap on concurrent `/ws` connections; excess sockets are politely closed with code 1013 ("try again later") so a connection flood cannot exhaust the box. |

### Message bus & dispatch (optional)

| Variable | Default | Effect |
| --- | --- | --- |
| `DM_USE_KAFKA` | `false` | Use Kafka instead of the in-memory bus. |
| `DM_KAFKA_BROKERS` / `DM_KAFKA_BACKUP` | `` | Primary/backup broker lists. |
| `DM_TWILIO_SID` / `DM_TWILIO_TOKEN` | `` | SMS dispatch (blank → dry-run). |
| `DM_FCM_KEY` | `` | Push dispatch (blank → dry-run). |
| `DM_IRIDIUM_URL` | `` | Satellite dispatch endpoint. |
| `DM_ANTHROPIC_KEY` | `` | LLM escalation layer (blank → deterministic template, no network). Alias `ANTHROPIC_API_KEY`. Uses `claude-opus-4-8` when set. |

---

## 6. Enabling persistence with a Railway Postgres plugin

By default DisasterMind keeps all state in memory; a redeploy or crash starts
fresh. To make resource state and telemetry durable:

1. In the project, **New → Database → Add PostgreSQL**. (PostGIS extensions are
   created lazily by the repo DDL where available; a plain Postgres works for
   the in-memory-degrading fallback path.)
2. Railway exposes connection variables on the database service. Reference them
   from the API service. The simplest mapping (resources + telemetry can share
   one database initially):

   | Set on the API service | To |
   | --- | --- |
   | `DM_PERSIST` | `1` |
   | `DM_POSTGRES_DSN` | the plugin's connection string (`${{Postgres.DATABASE_URL}}`) |
   | `DM_TIMESCALE_DSN` | the plugin's connection string (or a second DB) |

3. (Optional) Add an Elasticsearch plugin / external cluster and set
   `DM_ELASTICSEARCH_URL` to enable the searchable audit trail. Left blank, the
   audit trail still hash-chains to the local `DM_AUDIT_LOG` JSONL.
4. Redeploy. Watch `/readyz` and the RUNBOOK "DB unreachable → degraded
   in-memory" entry. If a backend is down, the box still serves — it just logs
   the degradation and uses the in-memory fallback for that repo.

> Railway containers have an ephemeral filesystem. `DM_AUDIT_LOG` (a file) does
> NOT survive redeploys — rely on `DM_ELASTICSEARCH_URL` and the Postgres
> plugins for durability, and treat the JSONL as a local breadcrumb.

---

## 7. Turning on API auth + the `/ws ?token=` note

1. Generate one or more strong tokens (e.g. `openssl rand -hex 32`).
2. Set `DM_API_KEYS=<token>` (or `DM_API_KEYS_MAP=commander:<token>`). Auth turns
   on automatically; all data routes now require `Authorization: Bearer <token>`
   (the `X-Api-Key: <token>` header is also accepted).
3. **WebSocket auth — the gotcha.** Browsers cannot set an `Authorization`
   header on a `new WebSocket()` connection. The security middleware therefore
   ALSO reads the token from the query string. Connect with:

   ```
   wss://<your-app>.up.railway.app/ws?token=<your-token>
   ```

   (`?api_key=<token>` is accepted as an alias.) A missing/invalid token closes
   the socket with code 1008 (policy violation); over-limit principals get
   HTTP 429 / WS close.
4. Set `DM_CORS_ORIGINS` to your web-console origin(s) once you stop using
   the bundled same-origin static UI.

---

## 8. Verify the deploy

After **Generate Domain**, from your laptop:

```bash
BASE=https://<your-app>.up.railway.app
curl -fsS  $BASE/healthz   # {"status":"alive","live":true,...}     -> liveness
curl -fsS  $BASE/readyz    # 200 {"status":"ready",...} when wired   -> readiness
curl -fsS  $BASE/health    # legacy snapshot (also the Railway healthcheck path)
curl -fsS  $BASE/metrics   # Prometheus text exposition
curl -fsS  $BASE/topics    # {"topic": count, ...} once the loop has ticked

# With auth on:
curl -fsS -H "Authorization: Bearer $TOKEN" $BASE/incidents
```

If `/topics` is all zeros, the loop driver is not advancing — see step 6 and the
RUNBOOK. If `/readyz` returns 503, the system is up but not ready (a module is
degraded) — that is expected during a backend outage and is documented in the
RUNBOOK.

See **RUNBOOK.md** for failure modes, scaling, `/metrics`, incident response,
and backup/restore.
