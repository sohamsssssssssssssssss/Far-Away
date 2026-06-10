"""Factory for the routing module (PRD Step 5).

The orchestration layer calls :func:`build_agents` to instantiate every agent in
this module. Routing contributes a single Tier-2 :class:`EvacuationRoutingAgent`
that subscribes to ``Topic.CASCADE`` + ``Topic.RESOURCE_PLAN`` and publishes
``Topic.ROUTING_PLAN``.
"""
from __future__ import annotations

from typing import Any

from ...audit.decision_log import DecisionLogger
from ...core.bus import MessageBus
from .agent import EvacuationRoutingAgent


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger | None = None,
    settings: Any | None = None,
) -> list:
    """Instantiate and return all routing-module agents (list[BaseAgent])."""
    return [
        EvacuationRoutingAgent(bus=bus, logger=logger, settings=settings),
    ]
