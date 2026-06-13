"""Service wiring for the dashboard transport.

:func:`build_service` lazily wires a full DisasterMind system (or a graceful
fallback) and returns a framework-free :class:`DashboardService`; the route
factory in :mod:`.factory` builds a FastAPI app over whatever service it gets.
"""
import logging
from typing import Any

from ...audit.decision_log import DecisionLogger
from ...core.bus import InMemoryBus, MessageBus
from ...core.config import Settings
from ..service import DashboardService

log = logging.getLogger("disastermind.api.request")


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
        from ...orchestration.build import build_system

        loop = build_system(bus=bus, logger=logger, settings=settings)
        commander = getattr(loop, "commander", None)
    except Exception:  # pragma: no cover - defensive boot path (Step 10)
        commander = None
    if commander is None:  # last-ditch fallback so the dashboard still serves
        from ...tier1.commander.agent import CommanderAgent

        commander = CommanderAgent(bus=bus, logger=logger, settings=settings)
    service = DashboardService(bus=bus, commander=commander)
    service.start_streaming()
    return service
