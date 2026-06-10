"""Factory hooks for the API / dashboard package (PRD Step 7 + Step 10).

The API package is *infra-only*: it produces no bus agents (the dashboard reads
the bus and delegates to the existing Tier-1 Commander), so it does not extend
the agent DAG. We still expose :func:`build_agents` for interface parity with
the per-module factory contract — it returns an empty list. The useful entry
point is :func:`build_service`, which wires a :class:`DashboardService`.
"""
from __future__ import annotations

from ..audit.decision_log import DecisionLogger
from ..core.bus import MessageBus
from ..core.config import Settings
from .app import build_service
from .service import DashboardService

__all__ = ["build_agents", "build_service", "DashboardService"]


def build_agents(bus: MessageBus, logger: DecisionLogger, settings: Settings) -> list:
    """Infra-only package: contributes no bus agents (returns ``[]``)."""
    return []
