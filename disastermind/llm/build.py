"""Factory for the Group B LLM escalation layer (PRD Step 7).

Mirrors the uniform per-module factory contract used across DisasterMind: the
orchestration layer calls :func:`build_agents` to instantiate this package's
agents. The sole agent is the advisory :class:`EscalationNarrator`.
"""
from __future__ import annotations

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.config import Settings
from .narrator import EscalationNarrator


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
) -> list[BaseAgent]:
    """Instantiate and return the EscalationNarrator agent (PRD Step 7)."""
    return [EscalationNarrator(bus=bus, logger=logger, settings=settings)]
