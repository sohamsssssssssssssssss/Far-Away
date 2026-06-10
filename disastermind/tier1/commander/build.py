"""Factory for the Tier-1 Commander module (PRD Step 7).

The orchestration layer calls :func:`build_agents` to instantiate every agent in
this module. The Commander is the sole Tier-1 agent.
"""
from __future__ import annotations

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.config import Settings
from .agent import CommanderAgent


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
) -> list[BaseAgent]:
    """Instantiate and return the Commander agent (PRD Step 7)."""
    return [CommanderAgent(bus=bus, logger=logger, settings=settings)]
