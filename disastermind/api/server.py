"""Live Commander Dashboard *server* (PRD Step 7 dashboard + Step 10 WebSocket).

This module is the runnable front door for the dashboard. The transport-free
policy already lives in :class:`~disastermind.api.service.DashboardService` and a
thin FastAPI app is produced by :func:`disastermind.api.app.create_app`; this
file only *assembles* those pieces into a single served application and adds the
last mile — serving the single-file static UI and exposing a tidy run() entry.

Design (HARD RULE 2): importing this module is standard-library-only and never
touches the network. FastAPI and uvicorn are heavy/optional, so they are
imported **lazily inside functions**. :func:`create_server` always succeeds and
returns a :class:`DashboardServer` holding the live
:class:`~disastermind.api.service.DashboardService` and the wired
:class:`~disastermind.orchestration.loop.CoordinationLoop`; only when the caller
asks for ``.app`` / :meth:`DashboardServer.run` is FastAPI required.

The static dashboard (``static/index.html``) polls ``GET /topics`` and
``GET /escalations`` and opens the ``/ws`` WebSocket to stream bus messages live,
with approve/reject buttons hitting the POST endpoints from :mod:`api.app`.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.bus import InMemoryBus, MessageBus
from ..core.config import Settings
from .service import DashboardService

log = logging.getLogger("disastermind.api.server")

# Directory holding the single-file vanilla-JS dashboard we serve at "/".
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


def _env_flag(key: str, *, default: bool) -> bool:
    """Read a boolean env flag (``1/true/yes/on`` vs ``0/false/no/off``)."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class DashboardServer:
    """Assembled, runnable dashboard (PRD Step 7 + Step 10).

    Attributes
    ----------
    bus:
        The shared in-process / Kafka bus the whole system runs on.
    loop:
        The wired :class:`CoordinationLoop` (full agent DAG) from
        :func:`disastermind.orchestration.build.build_system`.
    service:
        The framework-free :class:`DashboardService` the UI talks to.

    The FastAPI ``app`` is built lazily on first access so this object can be
    constructed and unit-tested with the standard library alone (no FastAPI).
    """

    bus: MessageBus
    loop: Any  # CoordinationLoop — typed Any to avoid an orchestration import cycle
    service: DashboardService
    collector: Any = None  # observability MetricsCollector wired onto the bus (optional)
    _app: Any = None
    _driver: Any = None  # background loop-driver thread handle (only set in run())
    # Graceful-shutdown state (PRD Step 10). All inert until run()/shutdown():
    #   _shutdown_callbacks: operator-registered drain hooks, run in order.
    #   _shutdown: the ops.GracefulShutdown registry assembled in run() (lazy).
    #   _shutdown_done: idempotency guard so the drain runs exactly once.
    _shutdown_callbacks: list = field(default_factory=list)
    _shutdown: Any = None
    _shutdown_done: bool = False

    # ------------------------------------------------------------------ app/UI
    @property
    def app(self) -> Any:
        """The FastAPI application (built + cached lazily; needs FastAPI).

        Wraps :func:`disastermind.api.app.create_app` and additionally mounts
        the static dashboard at ``GET /`` (and ``GET /index.html``). Raises
        :class:`RuntimeError` (from ``create_app``) if FastAPI is absent.
        """
        if self._app is None:
            self._app = self._build_app()
        return self._app

    def _build_app(self) -> Any:
        from .app import create_app  # lazy: create_app itself lazily needs FastAPI

        # Pass the wired loop (for /readyz readiness) and the metrics collector
        # (for /metrics exposition) so the production endpoints reflect reality.
        app = create_app(self.service, loop=self.loop, collector=self.collector)
        self._mount_static(app)
        self._mount_security(app)
        self._mount_cors(app)  # added last -> outermost -> handles preflight before auth
        return app

    def _mount_cors(self, app: Any) -> None:
        """Allow the browser dashboard (a different origin) to call the API.

        Without CORS a deployed operator console cannot reach the backend at all.
        Origins come from ``DM_CORS_ORIGINS`` (comma-separated; default ``*`` for
        dev). Token auth travels in the ``Authorization`` header (not cookies), so
        credentialed mode is off and a ``*`` origin is permitted.
        """
        try:
            from fastapi.middleware.cors import CORSMiddleware
        except Exception:
            return
        import os

        raw = os.environ.get("DM_CORS_ORIGINS", "*").strip()
        origins = ["*"] if raw in ("", "*") else [o.strip() for o in raw.split(",") if o.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _mount_security(self, app: Any) -> None:
        """Optionally enforce API auth + rate limiting (PRD Step 7 hardening).

        OFF BY DEFAULT — enforced only when API tokens are configured via the
        environment (``DM_API_KEYS`` / ``DM_API_KEYS_MAP``); an unconfigured
        deployment stays fully open so existing routes/tests are unaffected. The
        static UI and ``/health`` stay open even when auth is on. Any failure
        here is swallowed so security setup can never break dashboard creation.
        """
        try:
            from ..security.auth import (
                authenticate,
                ROLE_ADMIN,
                ROLE_OPERATOR,
                ROLE_VIEWER,
                TokenStore,
            )
            from ..security.ratelimit import RateLimiter, client_ip, ip_rate_limiter
        except Exception:
            return

        store = TokenStore.from_env()
        if not getattr(store, "enabled", False):
            return  # default-open: no API keys configured

        limiter = RateLimiter()
        ip_limiter = ip_rate_limiter()
        rbac_on = bool(getattr(store, "rbac_enabled", False))

        # Admin-only route prefixes (matched after stripping an optional ``/v1``).
        # These are reserved for future privileged operations; any route under one
        # requires the ``admin`` scope. Kept additive so today's routes are
        # unaffected unless an operator deploys an admin surface.
        admin_prefixes = ("/admin",)

        def _required_scope(path: str, method: str) -> str:
            """Map a request to the RBAC scope it requires (PRD Step 7).

            GET/HEAD reads need ``viewer``; an approve/reject action needs
            ``operator``; anything under an admin prefix needs ``admin``.
            """
            p = path[len("/v1"):] if path.startswith("/v1/") or path == "/v1" else path
            if any(p == pre or p.startswith(pre + "/") for pre in admin_prefixes):
                return ROLE_ADMIN
            if method in ("GET", "HEAD", "OPTIONS"):
                return ROLE_VIEWER
            if p.endswith("/approve") or p.endswith("/reject"):
                return ROLE_OPERATOR
            # Any other mutating route defaults to operator (read+act tier).
            return ROLE_OPERATOR
        # Health/liveness/readiness and the Prometheus scrape endpoint must stay
        # reachable without a token so probes and scrapers work behind auth.
        open_paths = {
            "/",
            "/index.html",
            "/health",
            "/healthz",
            "/readyz",
            "/metrics",
            "/docs",
            "/openapi.json",
            "/redoc",
        }

        # Map each rejection status to the error ``type`` used in the app's JSON
        # envelope so security failures read the same as route errors.
        _err_types = {401: "unauthorized", 403: "forbidden", 429: "rate_limited"}

        def _ratelimit_headers(lim: Any, result: Any) -> list[tuple[bytes, bytes]]:
            """Standard ``X-RateLimit-*`` headers for a limiter check (PRD Step 7).

            Mirrors the GitHub/Stripe convention so clients can self-throttle:
            ``Limit`` is the bucket capacity (burst allowance), ``Remaining`` the
            tokens left after this check (floored to a whole number, never
            negative), and ``Reset`` a hint — in seconds — until the bucket is
            usable again (``retry_after`` on a deny, else 0 when tokens remain).
            Best-effort: a limiter missing any attribute simply omits that header
            so this can never break the request path.
            """
            out: list[tuple[bytes, bytes]] = []
            cap = getattr(lim, "capacity", None)
            if cap is not None:
                out.append((b"x-ratelimit-limit", str(int(cap)).encode()))
            remaining = getattr(result, "remaining", None)
            if remaining is not None:
                out.append(
                    (b"x-ratelimit-remaining", str(max(0, int(remaining))).encode())
                )
            reset = _retry_seconds(result) if not getattr(result, "allowed", True) else 0
            out.append((b"x-ratelimit-reset", str(reset).encode()))
            return out

        async def _reject(
            stype: str,
            send: Any,
            code: int,
            detail: str,
            retry: int | None = None,
            extra_headers: list[tuple[bytes, bytes]] | None = None,
        ) -> None:
            if stype == "websocket":
                await send({"type": "websocket.close", "code": 1008})  # policy violation
                return
            import json as _json

            headers = [(b"content-type", b"application/json")]
            if retry is not None:
                headers.append((b"retry-after", str(retry).encode()))
            if extra_headers:
                headers.extend(extra_headers)
            # Emit the standard ``{"error": {...}}`` envelope (PRD Step 7). The
            # legacy top-level ``detail`` is kept for back-compat with clients that
            # read it directly.
            body = _json.dumps(
                {
                    "error": {"type": _err_types.get(code, "error"), "detail": detail, "request_id": None},
                    "detail": detail,
                }
            ).encode()
            await send({"type": "http.response.start", "status": code, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        def _retry_seconds(result: Any) -> int:
            raw = getattr(result, "retry_after", 1)
            return 1 if raw in (None, float("inf")) else max(1, int(raw))

        # Pure-ASGI middleware: unlike BaseHTTPMiddleware (which only sees ``http``
        # scopes and so left the ``/ws`` live stream unauthenticated), this guards
        # BOTH ``http`` and ``websocket`` scopes.
        class _SecurityASGI:
            def __init__(self, inner: Any) -> None:
                self.inner = inner

            async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
                stype = scope.get("type")
                if stype not in ("http", "websocket"):
                    return await self.inner(scope, receive, send)
                if stype == "http" and scope.get("path", "") in open_paths:
                    return await self.inner(scope, receive, send)

                # (2) PER-IP rate limit FIRST — bounds an *unauthenticated* burst
                # from a single source before any token check, so a flood of
                # anonymous/invalid-token requests cannot be unbounded.
                ip_result = ip_limiter.check(client_ip(scope))
                if not ip_result.allowed:
                    return await _reject(
                        stype,
                        send,
                        429,
                        "rate limit exceeded (ip)",
                        _retry_seconds(ip_result),
                        extra_headers=_ratelimit_headers(ip_limiter, ip_result),
                    )

                headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
                raw = headers.get(b"authorization") or headers.get(b"x-api-key")
                token = raw.decode("latin-1") if raw else None
                if token is None:
                    # Browsers cannot set an Authorization header on a WebSocket
                    # (`new WebSocket()` allows no headers), so accept the token
                    # from the query string too: wss://host/ws?token=<key>.
                    from urllib.parse import parse_qs

                    qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
                    vals = qs.get("token") or qs.get("api_key")
                    token = vals[0] if vals else None
                principal = authenticate(token, store=store)
                if principal is None:
                    return await _reject(stype, send, 401, "unauthorized")

                # (1) RBAC: only enforced when scoped/role tokens are configured.
                # A role-flat store (plain DM_API_KEYS) skips this entirely, so the
                # existing auth tests/deployments are unchanged.
                if rbac_on:
                    needed = _required_scope(
                        scope.get("path", ""), scope.get("method", "GET")
                    )
                    if not principal.has_scope(needed):
                        return await _reject(
                            stype, send, 403, f"insufficient scope: requires '{needed}'"
                        )

                result = limiter.check(principal.name)
                rl_headers = _ratelimit_headers(limiter, result)
                if not result.allowed:
                    return await _reject(
                        stype,
                        send,
                        429,
                        "rate limit exceeded",
                        _retry_seconds(result),
                        extra_headers=rl_headers,
                    )

                # ALLOWED: surface the per-principal budget on the response so a
                # client can self-throttle. WebSocket scopes carry no response
                # headers, so only decorate the http path; we wrap ``send`` to
                # append the headers onto the route's ``response.start`` event.
                if stype == "websocket":
                    return await self.inner(scope, receive, send)

                async def _send_with_ratelimit(message: Any) -> Any:
                    if message.get("type") == "http.response.start":
                        message = dict(message)
                        message["headers"] = list(message.get("headers") or []) + rl_headers
                    return await send(message)

                return await self.inner(scope, receive, _send_with_ratelimit)

        app.add_middleware(_SecurityASGI)

    def _mount_static(self, app: Any) -> None:
        """Add routes that serve the single-file dashboard UI (PRD Step 7)."""
        try:
            from fastapi.responses import HTMLResponse, JSONResponse
        except Exception:  # pragma: no cover - create_app would already have raised
            return

        def _index() -> Any:
            html = self.index_html()
            if html is None:
                return JSONResponse(
                    {"detail": "dashboard UI not found"}, status_code=404
                )
            return HTMLResponse(html)

        # Serve the dashboard at the site root and at an explicit path.
        app.add_api_route("/", _index, methods=["GET"], include_in_schema=False)
        app.add_api_route(
            "/index.html", _index, methods=["GET"], include_in_schema=False
        )

    @staticmethod
    def index_html() -> str | None:
        """Return the dashboard HTML source, or ``None`` if missing (stdlib only)."""
        try:
            return INDEX_HTML.read_text(encoding="utf-8")
        except OSError:
            return None

    # ----------------------------------------------------------- loop driver
    def start_loop_driver(self, interval: float | None = None) -> bool:
        """Start a daemon thread that periodically drives the coordination loop.

        The deployed process serves the dashboard but, without this, never
        advances :meth:`CoordinationLoop.run_once`, so ingestion never ticks and
        the UI shows ``messages_seen=0``. This driver seeds the field-team roster
        ONCE (so dispatch can bind to real teams) and then calls ``run_once`` on a
        cadence, flowing real incident activity to the bus and out over ``/ws``.

        OPT-OUT via ``DM_API_DRIVE_LOOP`` (``0``/``false`` disables). The cadence
        is ``settings.loop_interval_seconds`` (default 30 s) unless a faster
        ``DM_API_TICK`` (seconds, may be fractional) is set. Returns ``True`` if a
        thread was started, ``False`` if driving is disabled or impossible.

        Idempotent and intentionally NOT called from :func:`create_server` — only
        the serving path (:meth:`run` / ``__main__``) starts threads, so unit
        tests stay deterministic and thread-free (HARD RULE 2).
        """
        if not _env_flag("DM_API_DRIVE_LOOP", default=True):
            return False
        if self.loop is None:
            log.warning("no coordination loop wired; not starting loop driver")
            return False
        if self._driver is not None and getattr(self._driver, "is_alive", lambda: False)():
            return True  # already running

        if interval is None:
            tick = os.environ.get("DM_API_TICK")
            if tick:
                try:
                    interval = float(tick)
                except (TypeError, ValueError):
                    interval = None
            if interval is None:
                interval = float(getattr(self.loop.settings, "loop_interval_seconds", 30) or 30)
        interval = max(0.05, float(interval))

        stop = threading.Event()

        def _drive() -> None:
            # Seed field teams once so the field tier can bind dispatch orders.
            try:
                from ..scenarios.base import seed_field_teams

                seed_field_teams(self.bus)
            except Exception:  # pragma: no cover - seeding is best-effort
                log.exception("loop driver: field-team seeding failed (continuing)")
            import time as _time

            # When DM_FEEDS_LIVE is set, pull REAL feeds (USGS quakes, Open-Meteo)
            # through the resilient poller each cycle — a per-feed circuit breaker
            # (persisted in `breakers`) keeps a flaky upstream from being hammered.
            # Default off: the synthetic sample stream keeps the dashboard lively.
            live_feeds = _env_flag("DM_FEEDS_LIVE", default=False)
            breakers: dict = {}
            while not stop.is_set():
                if live_feeds:
                    try:
                        from ..live.resilient import resilient_poll_feeds

                        resilient_poll_feeds(self.loop, live=True, breakers=breakers)
                    except Exception:  # pragma: no cover - a flaky feed must not kill the box
                        log.exception("loop driver: live feed poll failed (continuing)")
                try:
                    self.loop.run_once(_time.time())
                except Exception:  # pragma: no cover - a sick cycle must not kill the box
                    log.exception("loop driver: run_once failed (continuing)")
                stop.wait(interval)

        thread = threading.Thread(target=_drive, name="dm-api-loop-driver", daemon=True)
        thread._dm_stop = stop  # type: ignore[attr-defined]
        thread.start()
        self._driver = thread
        log.info("loop driver started (interval=%.3fs)", interval)
        return True

    def stop_loop_driver(self) -> None:
        """Signal the background loop driver to stop (best-effort; non-blocking)."""
        thread = self._driver
        if thread is None:
            return
        stop = getattr(thread, "_dm_stop", None)
        if stop is not None:
            stop.set()
        self._driver = None

    # ---------------------------------------------------------- graceful drain
    def register_shutdown_callback(self, callback: Any, *, name: str | None = None) -> Any:
        """Register a drain hook run (in registration order) during shutdown.

        PRD Step 10: deployments (Railway/k8s) send ``SIGTERM`` on every redeploy.
        Use this to flush state, close external clients, etc., before the process
        exits. Callbacks run inside :meth:`shutdown` (whether reached via a real
        signal or a direct test call) in the order registered, AFTER the loop
        driver is stopped and live WebSocket clients are asked to close. A
        callback that raises is logged and does not abort the remaining drain (we
        are tearing down — best-effort cleanup beats aborting halfway). Returns
        the callback so it can be used as a decorator. Purely additive: nothing is
        installed or run until :meth:`shutdown` (or a signal) fires.
        """
        if not callable(callback):
            raise TypeError("shutdown callback must be callable")
        label = name or getattr(callback, "__name__", repr(callback))
        self._shutdown_callbacks.append((label, callback))
        return callback

    def _signal_ws_close(self) -> None:
        """Ask every live ``/ws`` client to close cleanly (best-effort).

        Sets the shared ``app.state.ws_closing`` Event the WebSocket handlers poll
        each heartbeat cycle; they then close with code 1001 ("going away") so a
        redeploy drains clients instead of dropping them abruptly. No-op if the
        FastAPI app was never built (no sockets can be open) or the event is
        absent (older app); never raises so it cannot block the drain.
        """
        app = self._app  # only an already-built app can have live sockets
        if app is None:
            return
        try:
            event = getattr(getattr(app, "state", None), "ws_closing", None)
            if event is not None:
                event.set()
        except Exception:  # pragma: no cover - signalling must never block shutdown
            log.exception("failed to signal WebSocket clients to close (continuing)")

    def shutdown(self, reason: str = "manual") -> bool:
        """Drain the server for a clean exit — idempotent; safe WITHOUT a signal.

        PRD Step 10. Performs, exactly once, the work a ``SIGTERM``/``SIGINT``
        must trigger so an in-flight disaster response is not torn down abruptly:

        1. stop the background loop driver (stop accepting new coordination work);
        2. ask every live ``/ws`` client to close cleanly (1001 "going away");
        3. run each operator-registered drain callback in registration order,
           tolerating one that raises (the rest still run).

        uvicorn drains its own in-flight HTTP requests on the real signal; this
        method adds the loop-driver stop + WebSocket close + drain callbacks on
        top. Tests call it directly (no real signal needed). Returns ``True`` if
        this call performed the drain, ``False`` if shutdown already ran
        (idempotent — the drain never runs twice).
        """
        if self._shutdown_done:
            log.debug("shutdown already completed (reason=%s); ignoring %s", self.reason_or_none(), reason)
            return False
        self._shutdown_done = True
        log.info("dashboard server shutdown requested (reason=%s)", reason)

        # (1) Stop accepting new work: halt the coordination-loop driver.
        try:
            self.stop_loop_driver()
        except Exception:  # pragma: no cover - never abort the rest of the drain
            log.exception("stop_loop_driver failed during shutdown (continuing)")

        # (2) Signal live WebSocket clients to close cleanly.
        self._signal_ws_close()

        # (3) Run operator drain callbacks in order; one that raises must not
        # abort the rest (best-effort teardown).
        for name, cb in list(self._shutdown_callbacks):
            try:
                cb()
            except BaseException:  # noqa: BLE001 - drain must not abort on one failure
                log.exception("shutdown callback %s failed (continuing)", name)
        log.info("dashboard server shutdown drain complete (%d callback(s))", len(self._shutdown_callbacks))
        return True

    def reason_or_none(self) -> str | None:
        """Reason recorded by the installed :class:`GracefulShutdown`, if any."""
        return getattr(self._shutdown, "reason", None)

    # --------------------------------------------------------------------- run
    def run(self, host: str = "127.0.0.1", port: int = 8000, **kwargs: Any) -> None:
        """Serve the dashboard over HTTP/WebSocket (lazily imports uvicorn).

        PRD Step 10: this is the operational entry point used by
        ``python -m disastermind.api``. uvicorn is an optional, heavy dependency
        imported here so the package stays standard-library-only to import. The
        background coordination-loop driver is started here (the serving path)
        so live data flows; it is NOT started in :func:`create_server`.

        Graceful shutdown (Railway/k8s send ``SIGTERM`` on every redeploy): an
        :class:`~disastermind.ops.GracefulShutdown` is installed for the serving
        process so a ``SIGTERM``/``SIGINT`` runs :meth:`shutdown` (stop the loop
        driver, close live WebSockets, run drain callbacks). uvicorn additionally
        drains its own in-flight HTTP requests on the same signal. Handlers are
        installed ONLY here (the serving path), never at import or in
        :func:`create_server`, so unit tests stay signal-free (HARD RULE 2).
        """
        try:
            import uvicorn
        except Exception as exc:  # pragma: no cover - exercised only without uvicorn
            raise RuntimeError(
                "uvicorn is not installed; install 'uvicorn' to serve the "
                "DisasterMind dashboard, or use the DashboardService programmatically."
            ) from exc
        self.install_signal_handlers()
        self.start_loop_driver()
        try:
            uvicorn.run(self.app, host=host, port=port, **kwargs)
        finally:
            # Run our extra drain (loop driver / WS / callbacks) on the way out —
            # idempotent, so a prior signal-triggered shutdown is a no-op here.
            self.shutdown("run-exit")
            self.uninstall_signal_handlers()

    # ----------------------------------------------------------- signal arming
    def install_signal_handlers(self) -> bool:
        """Arm SIGTERM/SIGINT to call :meth:`shutdown` (serving path only).

        Wraps :class:`~disastermind.ops.GracefulShutdown`: a single drain callback
        (this server's :meth:`shutdown`) is registered and the handler installed.
        Opt-out via ``DM_API_SIGNAL_SHUTDOWN`` (``0``/``false``). NEVER called at
        import or from :func:`create_server`; installing a handler off the main
        thread (or where signals are unavailable) is swallowed by
        ``GracefulShutdown.install`` so this is safe to attempt anywhere. Returns
        ``True`` if a handler registry was armed, ``False`` if disabled/unavailable.
        """
        if not _env_flag("DM_API_SIGNAL_SHUTDOWN", default=True):
            return False
        try:
            from ..ops.shutdown import GracefulShutdown
        except Exception:  # pragma: no cover - ops always present, defensive
            return False
        gs = GracefulShutdown()
        gs.register(lambda: self.shutdown("signal"), name="dashboard-server-shutdown")
        gs.install()
        self._shutdown = gs
        return True

    def uninstall_signal_handlers(self) -> None:
        """Restore any signal handlers armed by :meth:`install_signal_handlers`."""
        gs = self._shutdown
        if gs is not None:
            try:
                gs.uninstall()
            except Exception:  # pragma: no cover - best-effort restore
                pass


def create_server(
    bus: MessageBus | None = None,
    logger: DecisionLogger | None = None,
    settings: Settings | None = None,
    *,
    start_streaming: bool = True,
) -> DashboardServer:
    """Wire a full DisasterMind system and return a :class:`DashboardServer`.

    PRD Step 7 + Step 10. Lazily imports
    :func:`disastermind.orchestration.build.build_system` so importing this
    module stays cheap and stdlib-only. If orchestration is unavailable we fall
    back to a bare in-memory bus + a stub Commander so the dashboard still serves
    (graceful degradation, PRD Step 10). FastAPI/uvicorn are NOT required here —
    only :attr:`DashboardServer.app` / :meth:`DashboardServer.run` need them.

    Parameters
    ----------
    bus, logger, settings:
        Optional shared infrastructure; sensible offline defaults are created.
    start_streaming:
        When true (default), the service subscribes to the bus so the ``/ws``
        endpoint streams live updates (PRD Step 10).
    """
    bus = bus or InMemoryBus()
    logger = logger or DecisionLogger.null()
    settings = settings or Settings()

    loop: Any = None
    commander: Any = None
    try:
        from ..orchestration.build import build_system

        loop = build_system(bus=bus, logger=logger, settings=settings)
        commander = getattr(loop, "commander", None)
    except Exception:  # pragma: no cover - defensive boot path (PRD Step 10)
        loop = None
        commander = None
    if commander is None:  # last-ditch fallback so the dashboard still serves
        from ..tier1.commander.agent import CommanderAgent

        commander = CommanderAgent(bus=bus, logger=logger, settings=settings)

    service = DashboardService(bus=bus, commander=commander)
    if start_streaming:
        service.start_streaming()

    collector = _resolve_collector(loop, bus, logger)
    return DashboardServer(bus=bus, loop=loop, service=service, collector=collector)


def _resolve_collector(loop: Any, bus: MessageBus, logger: DecisionLogger) -> Any:
    """Find (or lazily create) the observability collector that feeds /metrics.

    ``build_system`` already wires a :class:`MetricsCollector` subscribed to every
    topic, so prefer that live instance (no double counting). If the loop is
    absent or that agent is missing — and observability is importable — attach a
    fresh collector to the bus so ``/metrics`` still reflects real traffic. Any
    failure degrades to ``None`` (``/metrics`` then renders an empty document).
    """
    try:
        from ..observability.collector import MetricsCollector
    except Exception:  # pragma: no cover - observability optional
        return None
    for agent in list(getattr(loop, "agents", []) or []):
        if isinstance(agent, MetricsCollector):
            return agent
    try:
        return MetricsCollector(bus, logger)
    except Exception:  # pragma: no cover - never block server creation
        return None


def run(host: str = "127.0.0.1", port: int = 8000, **kwargs: Any) -> None:
    """Convenience: build a server over a fresh system and serve it (PRD Step 10)."""
    create_server().run(host=host, port=port, **kwargs)
