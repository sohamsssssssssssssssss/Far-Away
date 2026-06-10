"""Thin FastAPI transport for the Commander Dashboard (PRD Step 7 + Step 10).

This is the *transport* half of the dashboard; all policy lives in the
framework-free :class:`~disastermind.api.service.DashboardService`. FastAPI is an
optional, heavy dependency, so it is imported **lazily inside**
:func:`create_app` (HARD RULE 2): importing this module never requires FastAPI
and never touches the network. Environments without FastAPI still get the full
:class:`DashboardService` for programmatic / test use.

Endpoints (PRD Step 7 + production hardening):
  * ``GET  /health``                       — liveness snapshot (back-compat)
  * ``GET  /healthz``                       — process liveness (always 200 if up)
  * ``GET  /readyz``                        — readiness (200 only when wired)
  * ``GET  /metrics``                       — Prometheus text exposition
  * ``GET  /topics``                       — per-topic message counts
  * ``GET  /incidents``                    — recent incident roll-up (``?limit=&offset=``)
  * ``GET  /recent``                       — recent bus messages (``?limit=&offset=``)
  * ``GET  /escalations``                  — open escalations (``?limit=&offset=``)
  * ``POST /escalations/{id}/approve``     — human approves -> dispatch
  * ``POST /escalations/{id}/reject``      — human rejects  -> rejection ACK
  * ``WS   /ws``                           — live stream of new bus messages (Step 10)

Production middleware (opt-in, inert without FastAPI):
  * structured per-request logging with an ``X-Request-ID`` (generated if absent,
    echoed back), and a consistent JSON error envelope via exception handlers;
  * security headers (``X-Content-Type-Options``, ``X-Frame-Options``,
    ``Referrer-Policy``, and ``Strict-Transport-Security`` behind TLS).

Pagination is **backward compatible**: ``/incidents``, ``/recent`` and
``/escalations`` return a bare JSON array by default (no ``limit``/``offset``
query params), exactly as before. When a caller supplies ``?limit=`` and/or
``?offset=`` the response becomes the paginated envelope
``{"items": [...], "total": N, "limit": L, "offset": O}``. This keeps the
existing clients/tests green while giving new clients real pagination.

NOTE: this module deliberately does **not** use ``from __future__ import
annotations``. The WebSocket route's ``websocket: WebSocket`` parameter must be
resolvable by FastAPI's ``get_type_hints`` at route-registration time, but
``WebSocket`` is imported lazily inside :func:`create_app` (FastAPI is optional,
HARD RULE 2) and so is not visible in module globals. Stringified annotations
would therefore be unresolvable and FastAPI would treat ``websocket`` as a
required query field, breaking the ``/ws`` handshake. Eager (non-stringified)
annotations bind the real local ``WebSocket`` object instead. Python 3.10+ PEP
604 unions (``X | None``) used below evaluate natively at runtime.
"""
import asyncio
import logging
import os
import threading
import time
import uuid
from json import JSONDecodeError
from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.bus import InMemoryBus, MessageBus
from ..core.config import Settings
from .service import DashboardService

log = logging.getLogger("disastermind.api.request")

# A "default large" limit so that an unpaginated list view still returns the full
# recent window rather than truncating. Callers that pass ``?limit=`` override it.
_DEFAULT_PAGE_LIMIT = 1000

# Versioned mount prefix. Every data route is registered both unversioned (legacy
# back-compat alias) AND under this prefix (``/v1/...``) so new clients can pin a
# version while existing clients/tests keep working unchanged.
_API_V1 = "/v1"

# Generous default request-body ceiling (bytes). The dashboard's POSTs are tiny
# (approve/reject carry only query params), so this guards against accidental or
# hostile oversize bodies without ever clipping a legitimate request. Overridable
# via ``DM_MAX_BODY``.
_DEFAULT_MAX_BODY = 1 * 1024 * 1024  # 1 MiB

# WebSocket hardening defaults (overridable via env). A server-side heartbeat ping
# every ``DM_WS_PING`` seconds prunes dead/half-open clients; ``DM_WS_MAX`` caps
# concurrent live connections so a connection flood cannot exhaust the box.
_DEFAULT_WS_PING = 20.0
_DEFAULT_WS_MAX = 256


def _env_int(key: str, default: int) -> int:
    """Read a positive integer env var, falling back to ``default`` on any error."""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _env_float(key: str, default: float) -> float:
    """Read a positive float env var, falling back to ``default`` on any error."""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw.strip())
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _is_json_decode(exc: Any) -> bool:
    """True if a validation error was actually caused by unparseable JSON.

    FastAPI surfaces a malformed request body as a :class:`RequestValidationError`
    whose underlying cause is a :class:`json.JSONDecodeError`. We sniff both the
    direct cause chain and the per-error ``type``/``msg`` so we can return a clear
    400 ``invalid_json`` instead of an opaque 422 schema error.
    """
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, JSONDecodeError):
        return True
    errors = getattr(exc, "errors", None)
    try:
        rows = errors() if callable(errors) else []
    except Exception:  # pragma: no cover - defensive
        return False
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("type") in ("json_invalid", "value_error.jsondecode"):
            return True
        ctx_err = (row.get("ctx") or {}).get("error")
        if isinstance(ctx_err, JSONDecodeError):
            return True
    return False


def _find_persisted_storage(loop: Any) -> Any:
    """Return the StatePersistor's ``Storage`` facade if a persistor is wired.

    The history endpoints prefer a durable store: we locate the ``persistence.state``
    agent on ``loop.agents`` (the :class:`~disastermind.persistence.persistor.StatePersistor`)
    and hand back its ``.storage``. Returns ``None`` when no loop/persistor exists,
    in which case the routes fall back to the in-memory :class:`DashboardService`.
    """
    for agent in list(getattr(loop, "agents", []) or []):
        if getattr(agent, "name", "") == "persistence.state":
            storage = getattr(agent, "storage", None)
            if storage is not None:
                return storage
    return None


def build_service(
    bus: MessageBus | None = None,
    logger: DecisionLogger | None = None,
    settings: Settings | None = None,
) -> DashboardService:
    """Wire a full DisasterMind system and return a :class:`DashboardService`.

    Lazily imports :func:`disastermind.orchestration.build.build_system` so this
    module stays cheap to import. Falls back to a bare in-memory bus + a stub
    commander if orchestration is unavailable (PRD Step 10 graceful degradation).
    """
    bus = bus or InMemoryBus()
    logger = logger or DecisionLogger.null()
    settings = settings or Settings()
    commander: Any = None
    try:
        from ..orchestration.build import build_system

        loop = build_system(bus=bus, logger=logger, settings=settings)
        commander = getattr(loop, "commander", None)
    except Exception:  # pragma: no cover - defensive boot path (Step 10)
        commander = None
    if commander is None:  # last-ditch fallback so the dashboard still serves
        from ..tier1.commander.agent import CommanderAgent

        commander = CommanderAgent(bus=bus, logger=logger, settings=settings)
    service = DashboardService(bus=bus, commander=commander)
    service.start_streaming()
    return service


# --------------------------------------------------------------------- pagination
def _paginate(
    rows: list[dict[str, Any]],
    limit: int | None,
    offset: int | None,
) -> Any:
    """Return ``rows`` either as a bare list (legacy) or a paginated envelope.

    Backward compatible: when BOTH ``limit`` and ``offset`` are ``None`` the
    caller asked for the legacy shape and we return the list unchanged. As soon
    as either query parameter is supplied we return
    ``{"items", "total", "limit", "offset"}`` over the full ``rows`` set.
    """
    if limit is None and offset is None:
        return rows
    total = len(rows)
    off = max(0, int(offset)) if offset is not None else 0
    lim = _DEFAULT_PAGE_LIMIT if limit is None else max(0, int(limit))
    window = rows[off : off + lim]
    return {"items": window, "total": total, "limit": lim, "offset": off}


def create_app(
    service: DashboardService | None = None,
    *,
    loop: Any = None,
    collector: Any = None,
) -> Any:
    """Build and return a FastAPI ``app`` bound to ``service`` (PRD Step 7).

    Raises :class:`RuntimeError` if FastAPI is not installed — callers that only
    need policy should use :class:`DashboardService` directly. FastAPI is
    imported here (lazy) so the package imports under the standard library alone.

    Parameters
    ----------
    service:
        The framework-free policy layer the routes delegate to.
    loop:
        Optional built :class:`CoordinationLoop` used by ``/readyz`` to report
        readiness via :func:`disastermind.ops.health.readiness`. If ``None`` the
        readiness probe degrades gracefully (reports ``not_ready``).
    collector:
        Optional :class:`~disastermind.observability.collector.MetricsCollector`
        rendered by ``/metrics``. If ``None`` (or observability is unavailable)
        ``/metrics`` returns an empty-but-valid exposition document.
    """
    try:
        from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
        from fastapi.responses import JSONResponse, PlainTextResponse
    except Exception as exc:  # pragma: no cover - exercised only without FastAPI
        raise RuntimeError(
            "FastAPI is not installed; install 'fastapi' to serve the dashboard "
            "HTTP/WebSocket API, or use DashboardService directly (stdlib only)."
        ) from exc

    svc = service or build_service()
    app = FastAPI(title="DisasterMind Commander Dashboard", version="0.1.0")

    # Request-body ceiling (bytes). Oversize requests are rejected with 413 before
    # the route runs (see the body-size ASGI middleware below). Generous default so
    # legitimate tiny POSTs are never clipped; override with ``DM_MAX_BODY``.
    max_body = _env_int("DM_MAX_BODY", _DEFAULT_MAX_BODY)

    # ------------------------------------------------------------ health/ready
    @app.get("/health")
    def health() -> dict[str, Any]:
        return svc.health()

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        """Liveness: the process is up and serving. Always 200 if we get here."""
        return {"status": "alive", "live": True, "checks": {"process": "ok"}}

    @app.get("/readyz")
    def readyz() -> Any:
        """Readiness: 200 only when the coordination loop/system is wired.

        Delegates to :func:`disastermind.ops.health.readiness` over the loop the
        server wired in. A not-ready system returns HTTP 503 so a load balancer
        withholds traffic (but does not restart the process — that's liveness).

        When durable persistence is requested (``DM_PERSIST``/``DM_LIVE``), the
        probe ALSO pings the configured external backends via
        :func:`disastermind.integrations.health.ping_backends` and degrades to
        503 if any *configured* backend is ``down`` (a backend reported ``absent``
        — unconfigured / client lib missing — is ignored, so an offline-default
        deploy is unchanged). Lazy + defensive: any failure here leaves the base
        readiness verdict untouched and never raises.
        """
        try:
            from ..ops.health import readiness as _readiness

            report = _readiness(loop)
        except Exception:  # pragma: no cover - defensive: never take the box down
            report = {"status": "not_ready", "ready": False, "checks": {}}
        ready = bool(report.get("ready"))
        _maybe_check_backends(report)
        # A 'down' backend was folded into the report below; recompute readiness.
        ready = ready and report.get("backends_ok", True)
        report["ready"] = ready
        report["status"] = "ready" if ready else "not_ready"
        code = 200 if ready else 503
        return JSONResponse(report, status_code=code)

    def _maybe_check_backends(report: dict[str, Any]) -> None:
        """Fold a backend-reachability check into ``report`` when DM_PERSIST is on.

        Off by default (no ``DM_PERSIST``/``DM_LIVE``) so the offline readiness
        path is byte-for-byte unchanged. Never raises: a probe failure leaves
        ``backends_ok`` True so it can only *add* a fail signal, never invent one.
        """
        import os as _os

        persist_on = any(
            (_os.environ.get(k) or "").strip().lower() in {"1", "true", "yes", "on"}
            for k in ("DM_PERSIST", "DM_LIVE")
        )
        if not persist_on:
            return
        try:
            from ..integrations.health import DOWN, ping_backends

            settings = getattr(loop, "settings", None) or Settings()
            statuses = ping_backends(settings)
        except Exception:  # pragma: no cover - probe must never take the box down
            return
        down = sorted(name for name, state in statuses.items() if state == DOWN)
        report["backends"] = statuses
        report["backends_ok"] = not down
        checks = report.get("checks")
        if isinstance(checks, dict):
            checks["backends"] = "fail" if down else "ok"

    # ------------------------------------------------------------------ metrics
    @app.get("/metrics")
    def metrics() -> Any:
        """Prometheus text exposition from the wired MetricsCollector (Step 9)."""
        try:
            from ..observability.exposition import render

            body = render(collector) if collector is not None else ""
        except Exception:  # pragma: no cover - never let scraping crash the box
            body = ""
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    # Register each data route under BOTH the legacy unversioned path and the
    # ``/v1`` prefix (HARD RULE: additive/back-compat). We attach handlers via
    # ``app.add_api_route`` so the same callable backs both paths with one body.
    def _route(path: str, handler: Any, *, methods: list[str]) -> None:
        app.add_api_route(path, handler, methods=methods)
        app.add_api_route(_API_V1 + path, handler, methods=methods, include_in_schema=False)

    # ------------------------------------------------------------------- topics
    def topics() -> dict[str, int]:
        return svc.topic_counts()

    _route("/topics", topics, methods=["GET"])

    # ------------------------------------------------------------- list views
    def incidents(limit: int | None = None, offset: int | None = None) -> Any:
        """Per-incident roll-up. Bare list by default; paginated with ?limit/?offset."""
        return _paginate(svc.incidents(), limit, offset)

    _route("/incidents", incidents, methods=["GET"])

    def recent(limit: int | None = None, offset: int | None = None) -> Any:
        """Recent bus messages (newest last). Bare list by default; paginated on demand."""
        rows = svc.recent(_DEFAULT_PAGE_LIMIT)
        return _paginate(rows, limit, offset)

    _route("/recent", recent, methods=["GET"])

    def escalations(limit: int | None = None, offset: int | None = None) -> Any:
        """Open escalations. Bare list by default; paginated with ?limit/?offset."""
        return _paginate(svc.list_escalations(), limit, offset)

    _route("/escalations", escalations, methods=["GET"])

    # ------------------------------------------------------------- history (store)
    def history_incidents(limit: int | None = None, offset: int | None = None) -> Any:
        """Persisted incident roll-up.

        Prefers the StatePersistor's durable store (assets/telemetry) when wired;
        falls back to the in-memory bus roll-up otherwise. Same pagination envelope
        as the live ``/incidents`` view.
        """
        rows = _history_incident_rows()
        return _paginate(rows, limit, offset)

    _route("/history/incidents", history_incidents, methods=["GET"])

    def audit_search(
        q: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """Search the durable audit trail by free text ``q`` + ISO time range.

        Queries the StatePersistor's :class:`ElasticsearchAuditRepo` when present;
        otherwise searches the in-memory bus history (same matcher semantics).
        Reuses the ``?limit=&offset=`` pagination envelope.
        """
        rows = _audit_search_rows(q, start, end)
        return _paginate(rows, limit, offset)

    _route("/audit/search", audit_search, methods=["GET"])

    # ----------------------------------------------------- escalation actions
    def approve(
        report_id: str, request: Request, approver: str = "human"
    ) -> dict[str, Any]:
        key = request.headers.get("idempotency-key")
        result = svc.approve_idempotent(report_id, approver=approver, key=key)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=f"no pending escalation {report_id}")
        return result

    _route("/escalations/{report_id}/approve", approve, methods=["POST"])

    def reject(
        report_id: str, request: Request, approver: str = "human", note: str = ""
    ) -> dict[str, Any]:
        key = request.headers.get("idempotency-key")
        result = svc.reject_idempotent(report_id, approver=approver, note=note, key=key)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=f"no pending escalation {report_id}")
        return result

    _route("/escalations/{report_id}/reject", reject, methods=["POST"])

    # ------------------------------------------------- history backing queries
    def _history_incident_rows() -> list[dict[str, Any]]:
        """Durable incident roll-up from the persisted store, else the bus view."""
        storage = _find_persisted_storage(loop)
        if storage is not None:
            rows = _incidents_from_store(storage)
            if rows is not None:
                return rows
        return svc.history_incidents()

    def _audit_search_rows(
        q: str | None, start: str | None, end: str | None
    ) -> list[dict[str, Any]]:
        """Durable audit search via the persistor's repo, else the in-memory bus."""
        storage = _find_persisted_storage(loop)
        audit = getattr(storage, "audit", None) if storage is not None else None
        searcher = getattr(audit, "search", None)
        if callable(searcher):
            try:
                return list(searcher(q, start=start, end=end, size=_DEFAULT_PAGE_LIMIT))
            except Exception:  # pragma: no cover - never let a store hiccup 500 the route
                log.exception("audit search failed; falling back to in-memory bus")
        return svc.audit_search(q, start=start, end=end, size=_DEFAULT_PAGE_LIMIT)

    def _incidents_from_store(storage: Any) -> list[dict[str, Any]] | None:
        """Incident roll-up from the durable store, or ``None`` to fall back.

        The :class:`~disastermind.persistence.persistor.StatePersistor` mirrors
        every bus message into the audit repo (``index_record``), so the durable
        audit trail is the authoritative incident history. We pull the full audit
        set and roll it up by ``incident_id`` into the SAME row shape as the live
        :meth:`DashboardService.incidents` view (so the dashboard reuses one
        renderer). Returns ``None`` if the store has no searchable audit (the
        caller then degrades to the in-memory bus roll-up) and never raises — a
        store hiccup must not 500 the history route.
        """
        audit = getattr(storage, "audit", None)
        searcher = getattr(audit, "search", None)
        if not callable(searcher):
            return None
        try:
            docs = list(searcher(None, size=_DEFAULT_PAGE_LIMIT))
        except Exception:  # pragma: no cover - never let a store hiccup 500 the route
            log.exception("history incidents store query failed; falling back to bus")
            return None
        return _rollup_incidents(docs)

    def _rollup_incidents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group audit docs by ``incident_id`` (same shape as the live view)."""
        agg: dict[str, dict[str, Any]] = {}
        for doc in docs:
            iid = doc.get("incident_id")
            if not iid:
                continue
            topic = doc.get("topic")
            ts = doc.get("timestamp")
            entry = agg.get(iid)
            if entry is None:
                entry = {
                    "incident_id": iid,
                    "module": doc.get("module"),
                    "message_count": 0,
                    "topics": set(),
                    "last_timestamp": ts,
                    "last_topic": topic,
                }
                agg[iid] = entry
            entry["message_count"] += 1
            if topic is not None:
                entry["topics"].add(topic)
            # Audit docs may arrive unordered; keep the lexicographically-latest
            # ISO timestamp as the incident's "last activity".
            if ts is not None and (entry["last_timestamp"] is None or ts >= entry["last_timestamp"]):
                entry["last_timestamp"] = ts
                entry["last_topic"] = topic
        rows: list[dict[str, Any]] = []
        for entry in agg.values():
            row = dict(entry)
            row["topics"] = sorted(t for t in entry["topics"] if t is not None)
            rows.append(row)
        rows.sort(key=lambda r: (r["last_timestamp"] or ""))
        return rows

    # ---------------------------------------------------------- websocket /ws
    # WebSocket hardening state, resolved once per app (env-overridable). The
    # concurrency cap guards against a connection flood; the heartbeat interval
    # bounds how long an idle/half-open socket survives before a ping prunes it.
    ws_max = _env_int("DM_WS_MAX", _DEFAULT_WS_MAX)
    ws_ping = _env_float("DM_WS_PING", _DEFAULT_WS_PING)
    _ws_state: dict[str, int] = {"count": 0}
    _ws_count_lock = asyncio.Lock()

    # Graceful-shutdown signal for live WebSocket clients (PRD Step 10). A
    # threading.Event so DashboardServer.shutdown() can set it from the serving
    # thread without an event loop; each /ws handler polls it once per heartbeat
    # cycle (bounded by ``ws_ping``) and closes the socket cleanly (1001 "going
    # away") so a redeploy drains clients instead of dropping them abruptly. The
    # heartbeat timeout caps how soon a wedged idle socket notices the signal.
    _ws_closing = threading.Event()

    async def ws(websocket: WebSocket) -> None:
        """Stream new bus messages to the client (PRD Step 10 refresh, hardened).

        Each new bus message is queued by a service listener and drained to the
        socket. We bridge the synchronous bus callback into asyncio via the running
        loop so the bus is never blocked by a slow client. Hardening: a concurrent-
        connection cap (``DM_WS_MAX``) closes excess sockets, and a server-side
        heartbeat ping every ``DM_WS_PING`` seconds drops dead/half-open clients.
        """
        # Concurrency cap: refuse (politely close) once at capacity so a flood of
        # sockets cannot exhaust the process. Accept first so the close code lands.
        async with _ws_count_lock:
            if _ws_state["count"] >= ws_max:
                await websocket.accept()
                await websocket.close(code=1013)  # 1013 = "try again later"
                return
            _ws_state["count"] += 1

        await websocket.accept()
        loop_ = asyncio.get_event_loop()
        queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()

        def _push(payload: dict[str, Any]) -> None:
            loop_.call_soon_threadsafe(queue.put_nowait, payload)

        unsubscribe = svc.add_listener(_push)
        try:
            # If a shutdown was requested before we even accepted, close at once.
            if _ws_closing.is_set():
                await websocket.close(code=1001)  # 1001 = "going away"
                return
            # Send an initial snapshot so a fresh client is not blank.
            await websocket.send_json({"kind": "snapshot", "topics": svc.topic_counts()})
            while True:
                if _ws_closing.is_set():
                    # Graceful shutdown in progress: close cleanly so the client
                    # can reconnect to the next instance instead of seeing a drop.
                    await websocket.close(code=1001)  # 1001 = "going away"
                    return
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=ws_ping)
                except asyncio.TimeoutError:
                    # Idle period elapsed: re-check the shutdown flag, then send a
                    # heartbeat. A dead/half-open peer raises here, dropping the
                    # connection (the finally cleans up).
                    if _ws_closing.is_set():
                        await websocket.close(code=1001)  # 1001 = "going away"
                        return
                    await websocket.send_json({"kind": "ping", "ts": time.time()})
                    continue
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        except Exception:  # a broken socket must not leak the listener slot
            pass
        finally:
            unsubscribe()
            async with _ws_count_lock:
                _ws_state["count"] = max(0, _ws_state["count"] - 1)

    app.add_api_websocket_route("/ws", ws)
    app.add_api_websocket_route(_API_V1 + "/ws", ws)

    # ----------------------------------------------- request id + logging mw
    @app.middleware("http")
    async def _request_context(request: Request, call_next: Any) -> Any:
        """Attach an ``X-Request-ID`` and emit one structured log line per request.

        The id is taken from an inbound ``X-Request-ID`` header when present (so a
        front proxy can correlate) and generated otherwise; it is stashed on
        ``request.state`` for the error handlers and echoed on every response.
        """
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Let the registered exception handler build the JSON envelope; it
            # also stamps the request id. Re-raise after logging the failure.
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            log.error(
                "request error",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(elapsed_ms, 2),
                },
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(elapsed_ms, 2),
            },
        )
        return response

    # Content-Security-Policy for the single-file dashboard UI. The UI is a vanilla
    # inline single-page app (inline <script>/<style>, same-origin fetch + a /ws
    # WebSocket), so the default policy allows self + inline scripts/styles and
    # restricts connect/img/font to the same origin (plus ws:/wss: for the live
    # stream). Override wholesale with ``DM_CSP``; set it empty to disable.
    _DEFAULT_CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    csp_header = os.environ.get("DM_CSP")
    if csp_header is None:
        csp_header = _DEFAULT_CSP

    # ----------------------------------------------------- security headers mw
    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Any) -> Any:
        """Add baseline hardening headers; CSP always, HSTS only when TLS."""
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # CSP locks down the single-page UI's resource origins. Skipped only when
        # explicitly disabled (``DM_CSP=""``) so an operator can opt out.
        if csp_header:
            headers.setdefault("Content-Security-Policy", csp_header)
        # Trust a terminating proxy's scheme hint (Railway/Heroku set this) as
        # well as the direct scheme so HSTS is asserted behind TLS only.
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "https":
            headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    # ------------------------------------------------- request body size guard
    # Pure-ASGI middleware so we can reject an oversize body BEFORE the route (or
    # FastAPI's body parsing) ever reads it. Two layers: a fast ``Content-Length``
    # check for well-behaved clients, and a streaming byte counter that also stops
    # a chunked/untruthful upload once it exceeds ``max_body``. Returns the same
    # JSON error envelope with HTTP 413 so clients get a consistent shape.
    class _BodySizeLimitASGI:
        def __init__(self, inner: Any, *, limit: int) -> None:
            self.inner = inner
            self.limit = limit

        async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
            if scope.get("type") != "http":
                return await self.inner(scope, receive, send)
            # Fast path: trust a declared Content-Length to reject early.
            for k, v in scope.get("headers") or []:
                if k == b"content-length":
                    try:
                        if int(v.decode("latin-1")) > self.limit:
                            return await self._too_large(scope, send)
                    except (ValueError, UnicodeDecodeError):
                        pass  # malformed header -> let the streaming guard catch it
                    break

            seen = 0
            too_large = False

            async def _counting_receive() -> Any:
                nonlocal seen, too_large
                event = await receive()
                if event.get("type") == "http.request":
                    seen += len(event.get("body", b"") or b"")
                    if seen > self.limit:
                        too_large = True
                return event

            # Wrap ``send`` so that if the streaming guard trips mid-body we never
            # let the route's own response start; we emit the 413 envelope instead.
            started = False

            async def _guarded_send(message: Any) -> Any:
                nonlocal started
                if message.get("type") == "http.response.start":
                    started = True
                return await send(message)

            try:
                await self.inner(scope, _counting_receive, _guarded_send)
            except Exception:
                if too_large and not started:
                    return await self._too_large(scope, send)
                raise
            if too_large and not started:  # pragma: no cover - defensive
                return await self._too_large(scope, send)

        async def _too_large(self, scope: Any, send: Any) -> None:
            import json as _json

            rid = None
            for k, v in scope.get("headers") or []:
                if k == b"x-request-id":
                    rid = v.decode("latin-1")
                    break
            body = _json.dumps(
                {
                    "error": {
                        "type": "payload_too_large",
                        "detail": f"request body exceeds {self.limit} bytes",
                        "request_id": rid,
                    }
                }
            ).encode()
            headers = [(b"content-type", b"application/json")]
            if rid:
                headers.append((b"x-request-id", rid.encode("latin-1")))
            await send({"type": "http.response.start", "status": 413, "headers": headers})
            await send({"type": "http.response.body", "body": body})

    app.add_middleware(_BodySizeLimitASGI, limit=max_body)

    # -------------------------------------------------- JSON error envelope
    def _envelope(request: Request, status_code: int, etype: str, detail: str) -> Any:
        request_id = getattr(request.state, "request_id", None)
        payload = {
            "error": {"type": etype, "detail": detail, "request_id": request_id}
        }
        resp = JSONResponse(payload, status_code=status_code)
        if request_id:
            resp.headers["X-Request-ID"] = request_id
        return resp

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> Any:
        return _envelope(
            request, exc.status_code, "http_error", str(exc.detail)
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception) -> Any:
        log.exception(
            "unhandled exception",
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return _envelope(
            request, 500, "internal_error", "internal server error"
        )

    try:  # request-validation errors -> same envelope (FastAPI/Starlette specific)
        from fastapi.exceptions import RequestValidationError

        @app.exception_handler(RequestValidationError)
        async def _validation_exc(request: Request, exc: RequestValidationError) -> Any:
            # A body that does not parse as JSON surfaces here as a wrapped
            # JSONDecodeError; report it as a 400 with a clear type so clients can
            # distinguish "malformed JSON" from ordinary schema validation (422).
            if _is_json_decode(exc):
                return _envelope(request, 400, "invalid_json", "request body is not valid JSON")
            return _envelope(request, 422, "validation_error", str(exc.errors()))
    except Exception:  # pragma: no cover - older/newer FastAPI without this export
        pass

    @app.exception_handler(JSONDecodeError)
    async def _json_decode_exc(request: Request, exc: JSONDecodeError) -> Any:
        """A raw JSON parse failure (``request.json()``) -> 400 envelope."""
        return _envelope(request, 400, "invalid_json", "request body is not valid JSON")

    # Stash references so tests / callers can introspect off the app.
    app.state.service = svc
    app.state.loop = loop
    app.state.collector = collector
    # Graceful-shutdown handle: DashboardServer.shutdown() sets this Event to ask
    # every live /ws client to close cleanly (1001) before the process exits.
    app.state.ws_closing = _ws_closing
    return app
