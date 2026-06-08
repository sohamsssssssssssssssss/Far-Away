"""Factory for the Tier 2 Resource-Optimisation module (PRD Step 4).

The orchestration layer calls :func:`build_agents` to instantiate every agent
this module owns. Resource allocation sits on the load-bearing
``prediction -> resource -> field -> commander -> dispatch`` chain: the
:class:`~disastermind.tier2.resource.agent.ResourceAllocationAgent` is the only
consumer of :data:`~disastermind.core.contracts.Topic.PREDICTION` that emits
:data:`~disastermind.core.contracts.Topic.RESOURCE_PLAN` (and, in turn, the
demand ``zones`` the routing tier needs).

A single equity-weighted allocator covers all modules (``Module.ALL``); it
also consumes :data:`~disastermind.core.contracts.Topic.CASCADE` for
reachability context. It ships with a small pre-positioned asset inventory so
the e2e pipeline runs offline (PRD Step 10). The optimiser degrades from PuLP
LP to a stdlib greedy assignment when no solver backend is present.
"""
from __future__ import annotations

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.config import Settings
from .agent import ResourceAllocationAgent


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
) -> list[BaseAgent]:
    """Instantiate and return the resource-optimisation agents (PRD Step 4)."""
    return [
        ResourceAllocationAgent(bus=bus, logger=logger, settings=settings),
    ]
