"""The FastAPI app assembler: :func:`create_app`.

This is the route factory half of the dashboard transport. All policy lives in
the framework-free :class:`~disastermind.api.service.DashboardService`; this
module only wires HTTP/WebSocket routes, middleware and exception handlers over
it. FastAPI is imported **lazily inside** :func:`create_app` (HARD RULE 2) so
importing this module never requires FastAPI and never touches the network.

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
import os
import threading
import time
import uuid
from json import JSONDecodeError
from typing import Any

from ...core.config import Settings
from ..service import DashboardService
from ._constants import (
    _API_V1,
    _DEFAULT_MAX_BODY,
    _DEFAULT_PAGE_LIMIT,
    _DEFAULT_WS_MAX,
    _DEFAULT_WS_PING,
)
from ._env import _env_float, _env_int
from ._errors import _is_json_decode
from ._persistence import _find_persisted_storage
from .pagination import _paginate
from .service import build_service, log


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
        h = svc.health()
        # Surface any simulated degradation (resilience demo) so the dashboard can
        # show it inline; absent/empty by default so the normal payload is unchanged.
        deg = getattr(app.state, "demo", {}).get("degraded", []) if hasattr(app.state, "demo") else []
        if deg:
            h = {**h, "degraded_components": deg}
        return h

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
            from ...ops.health import readiness as _readiness

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
            from ...integrations.health import DOWN, ping_backends

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
            from ...observability.exposition import render

            body = render(collector) if collector is not None else ""
        except Exception:  # pragma: no cover - never let scraping crash the box
            body = ""
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    # ------------------------------------------------------------ degraded-mode demo
    # A pitch/ops affordance (PRD Step 10 resilience): mark components as
    # simulated-down so the dashboard can SHOW the system losing a feed/broker and
    # *still coordinating* on fallbacks. The whole point is resilience, so the
    # system stays ``operational`` — this only annotates which components are
    # degraded; it does not disable the real pipeline. Honest, reversible, in-memory.
    _DEMO_COMPONENTS = {"usgs", "imd", "open-meteo", "firms", "kafka", "postgis",
                        "timescale", "elasticsearch", "prediction", "routing"}

    def _demo_state() -> dict[str, Any]:
        st = getattr(app.state, "demo", None)
        if st is None:
            st = {"degraded": []}
            app.state.demo = st
        return st

    def demo_status() -> dict[str, Any]:
        deg = _demo_state()["degraded"]
        return {
            "degraded_components": deg,
            "operational": True,  # resilience: degraded != down — we keep coordinating
            "mode": "degraded" if deg else "nominal",
            "known_components": sorted(_DEMO_COMPONENTS),
        }

    def demo_degrade(component: str = "", active: bool = True, reset: bool = False) -> dict[str, Any]:
        """Toggle a simulated component failure for the resilience demo.

        ``?component=usgs&active=true`` marks USGS degraded; ``active=false`` clears
        it; ``?reset=true`` clears all. The system remains operational throughout.
        """
        st = _demo_state()
        deg: list[str] = st["degraded"]
        if reset:
            deg.clear()
        elif component:
            c = component.strip().lower()
            if active and c not in deg:
                deg.append(c)
            elif not active and c in deg:
                deg.remove(c)
        return demo_status()

    app.add_api_route("/demo/status", demo_status, methods=["GET"])
    app.add_api_route("/demo/degrade", demo_degrade, methods=["POST"])

    # Register each data route under BOTH the legacy unversioned path and the
    # ``/v1`` prefix (HARD RULE: additive/back-compat). We attach handlers via
    # ``app.add_api_route`` so the same callable backs both paths with one body.
    def _route(path: str, handler: Any, *, methods: list[str]) -> None:
        app.add_api_route(path, handler, methods=methods)
        app.add_api_route(_API_V1 + path, handler, methods=methods, include_in_schema=False)

    # ------------------------------------------------ validation: cyclone backtest
    _cyclone_cache: dict[str, Any] = {}

    def validation_cyclone() -> Any:
        """National cyclone backtest metrics over all real IBTrACS landfalling storms.

        Wraps :func:`disastermind.hindcast.cyclone_backtest.run_national_backtest`
        (lazy; cached after first build). The same JSON the Evidence map renders —
        92 real storms, per-region activation, honest 'unknown' accounting.
        """
        if "data" not in _cyclone_cache:
            try:
                from ...hindcast.cyclone_backtest import run_national_backtest

                _cyclone_cache["data"] = run_national_backtest().to_dict()
            except Exception:  # pragma: no cover - never take the box down
                return JSONResponse({"error": "cyclone backtest unavailable"}, status_code=503)
        return _cyclone_cache["data"]

    _route("/validation/cyclone", validation_cyclone, methods=["GET"])

    # ------------------------------------------------ post-incident report (Step 9)
    def report_generate() -> Any:
        """Generate a post-incident report from the live system's audit + bus.

        Replaces the frontend's insecure browser->Anthropic call: the Anthropic
        key stays SERVER-SIDE (never shipped to the client), the model is
        ``claude-opus-4-8`` via the llm layer, and it degrades to a deterministic
        template + the always-available structured report when no key is set.
        Returns ``{markdown, report, narrative, narrative_source}``.
        """
        try:
            from ...reporting import IncidentReporter

            bus = getattr(loop, "bus", None)
            lg = getattr(loop, "logger", None)
            report = IncidentReporter(bus=bus, logger=lg).generate()
            out: dict[str, Any] = {"report": report.to_dict(), "markdown": report.to_markdown()}
        except Exception:  # pragma: no cover - never take the box down
            return JSONResponse({"error": "report unavailable"}, status_code=503)
        # Executive summary. With a real Anthropic key (server-side) we ask
        # claude-opus-4-8 for prose; otherwise (TemplateClient echoes its prompt)
        # we synthesise a deterministic summary from the report itself — never an
        # echoed instruction.
        rpt = out["report"]
        deterministic = (
            f"Incident {rpt.get('incident_id') or 'n/a'}: {rpt.get('message_count', 0)} "
            f"messages, {len(rpt.get('decisions', []))} decisions, "
            f"{len(rpt.get('escalations', []))} escalations, "
            f"{len(rpt.get('dispatch', []))} dispatch orders. "
            "See the full report for the timeline, equity-weighted allocation and "
            "SHAP-attributed predictions."
        )
        try:
            from ...llm.client import make_client

            client = make_client(getattr(loop, "settings", None))
            if getattr(client, "name", "template") == "anthropic":
                out["narrative"] = client.generate(
                    "Write a concise (<=150 word) post-incident executive summary for "
                    "an emergency commander, from this report:\n\n" + out["markdown"]
                )
                out["narrative_source"] = "anthropic"
            else:
                out["narrative"] = deterministic
                out["narrative_source"] = "template"
        except Exception:  # pragma: no cover - narrative is best-effort
            out["narrative"] = deterministic
            out["narrative_source"] = "template"
        return out

    _route("/report/generate", report_generate, methods=["POST", "GET"])

    # ------------------------------------------------ LLM proxy (server-side key)
    async def llm_generate(request: Request) -> Any:
        """Server-side LLM proxy for the frontend's ``callLLM`` (report generator).

        The browser must NOT call Anthropic directly (it would ship the key in the
        bundle and needs the dangerous-direct-browser-access header). This proxies
        ``{messages:[{role,content}]}`` to ``claude-opus-4-8`` with the key held
        SERVER-SIDE, returning ``{text, source}``. With no key configured it
        returns 503 so the caller falls back to its own deterministic report —
        we never fake LLM prose.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list) or not messages:
            return JSONResponse({"error": "messages[] required"}, status_code=400)
        system = " ".join(
            str(m.get("content", "")) for m in messages if m.get("role") == "system"
        )
        user = "\n".join(
            str(m.get("content", "")) for m in messages if m.get("role") != "system"
        )
        prompt = (system + "\n\n" + user).strip()
        try:
            from ...llm.client import make_client

            client = make_client(getattr(loop, "settings", None))
            if getattr(client, "name", "template") != "anthropic":
                return JSONResponse(
                    {"error": "LLM not configured (set DM_ANTHROPIC_KEY); use local fallback",
                     "source": "none"},
                    status_code=503,
                )
            return {"text": client.generate(prompt), "source": "anthropic"}
        except Exception:  # pragma: no cover - upstream failure -> caller falls back
            return JSONResponse({"error": "LLM call failed", "source": "error"}, status_code=502)

    _route("/llm/generate", llm_generate, methods=["POST"])

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
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

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
                except TimeoutError:
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
