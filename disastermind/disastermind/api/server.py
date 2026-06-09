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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.bus import InMemoryBus, MessageBus
from ..core.config import Settings
from .service import DashboardService

# Directory holding the single-file vanilla-JS dashboard we serve at "/".
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


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
    _app: Any = None

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

        app = create_app(self.service)
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
            from ..security.auth import authenticate, TokenStore
            from ..security.ratelimit import RateLimiter
        except Exception:
            return

        store = TokenStore.from_env()
        if not getattr(store, "enabled", False):
            return  # default-open: no API keys configured

        limiter = RateLimiter()
        open_paths = {"/", "/index.html", "/health", "/docs", "/openapi.json", "/redoc"}

        async def _reject(stype: str, send: Any, code: int, detail: str, retry: int | None = None) -> None:
            if stype == "websocket":
                await send({"type": "websocket.close", "code": 1008})  # policy violation
                return
            import json as _json

            headers = [(b"content-type", b"application/json")]
            if retry is not None:
                headers.append((b"retry-after", str(retry).encode()))
            await send({"type": "http.response.start", "status": code, "headers": headers})
            await send({"type": "http.response.body", "body": _json.dumps({"detail": detail}).encode()})

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
                headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
                raw = headers.get(b"authorization") or headers.get(b"x-api-key")
                token = raw.decode("latin-1") if raw else None
                principal = authenticate(token, store=store)
                if principal is None:
                    return await _reject(stype, send, 401, "unauthorized")
                result = limiter.check(principal.name)
                if not result.allowed:
                    raw_retry = getattr(result, "retry_after", 1)
                    retry = 1 if raw_retry in (None, float("inf")) else max(1, int(raw_retry))
                    return await _reject(stype, send, 429, "rate limit exceeded", retry)
                return await self.inner(scope, receive, send)

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

    # --------------------------------------------------------------------- run
    def run(self, host: str = "127.0.0.1", port: int = 8000, **kwargs: Any) -> None:
        """Serve the dashboard over HTTP/WebSocket (lazily imports uvicorn).

        PRD Step 10: this is the operational entry point used by
        ``python -m disastermind.api``. uvicorn is an optional, heavy dependency
        imported here so the package stays standard-library-only to import.
        """
        try:
            import uvicorn
        except Exception as exc:  # pragma: no cover - exercised only without uvicorn
            raise RuntimeError(
                "uvicorn is not installed; install 'uvicorn' to serve the "
                "DisasterMind dashboard, or use the DashboardService programmatically."
            ) from exc
        uvicorn.run(self.app, host=host, port=port, **kwargs)


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
    return DashboardServer(bus=bus, loop=loop, service=service)


def run(host: str = "127.0.0.1", port: int = 8000, **kwargs: Any) -> None:
    """Convenience: build a server over a fresh system and serve it (PRD Step 10)."""
    create_server().run(host=host, port=port, **kwargs)
