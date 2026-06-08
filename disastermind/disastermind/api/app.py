"""Thin FastAPI transport for the Commander Dashboard (PRD Step 7 + Step 10).

This is the *transport* half of the dashboard; all policy lives in the
framework-free :class:`~disastermind.api.service.DashboardService`. FastAPI is an
optional, heavy dependency, so it is imported **lazily inside**
:func:`create_app` (HARD RULE 2): importing this module never requires FastAPI
and never touches the network. Environments without FastAPI still get the full
:class:`DashboardService` for programmatic / test use.

Endpoints (PRD Step 7):
  * ``GET  /health``                       — liveness snapshot
  * ``GET  /topics``                       — per-topic message counts
  * ``GET  /incidents``                    — recent bus messages (``?limit=``)
  * ``GET  /escalations``                  — open escalations awaiting a human
  * ``POST /escalations/{id}/approve``     — human approves -> dispatch
  * ``POST /escalations/{id}/reject``      — human rejects  -> rejection ACK
  * ``WS   /ws``                           — live stream of new bus messages (Step 10)

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
from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.bus import InMemoryBus, MessageBus
from ..core.config import Settings
from .service import DashboardService


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


def create_app(service: DashboardService | None = None) -> Any:
    """Build and return a FastAPI ``app`` bound to ``service`` (PRD Step 7).

    Raises :class:`RuntimeError` if FastAPI is not installed — callers that only
    need policy should use :class:`DashboardService` directly. FastAPI is
    imported here (lazy) so the package imports under the standard library alone.
    """
    try:
        from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    except Exception as exc:  # pragma: no cover - exercised only without FastAPI
        raise RuntimeError(
            "FastAPI is not installed; install 'fastapi' to serve the dashboard "
            "HTTP/WebSocket API, or use DashboardService directly (stdlib only)."
        ) from exc

    svc = service or build_service()
    app = FastAPI(title="DisasterMind Commander Dashboard", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return svc.health()

    @app.get("/topics")
    def topics() -> dict[str, int]:
        return svc.topic_counts()

    @app.get("/incidents")
    def incidents(limit: int | None = None) -> list[dict[str, Any]]:
        return svc.recent(limit)

    @app.get("/escalations")
    def escalations() -> list[dict[str, Any]]:
        return svc.list_escalations()

    @app.post("/escalations/{report_id}/approve")
    def approve(report_id: str, approver: str = "human") -> dict[str, Any]:
        result = svc.approve(report_id, approver=approver)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=f"no pending escalation {report_id}")
        return result

    @app.post("/escalations/{report_id}/reject")
    def reject(report_id: str, approver: str = "human", note: str = "") -> dict[str, Any]:
        result = svc.reject(report_id, approver=approver, note=note)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=f"no pending escalation {report_id}")
        return result

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        """Stream new bus messages to the client (PRD Step 10 refresh).

        Each new bus message is queued by a service listener and drained to the
        socket. We bridge the synchronous bus callback into asyncio via the
        running loop so the bus is never blocked by a slow client.
        """
        import asyncio

        await websocket.accept()
        loop = asyncio.get_event_loop()
        queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()

        def _push(payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        unsubscribe = svc.add_listener(_push)
        try:
            # Send an initial snapshot so a fresh client is not blank.
            await websocket.send_json({"kind": "snapshot", "topics": svc.topic_counts()})
            while True:
                payload = await queue.get()
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()

    # Stash the service so tests / callers can introspect it off the app.
    app.state.service = svc
    return app
