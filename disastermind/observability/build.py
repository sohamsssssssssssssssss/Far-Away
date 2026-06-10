"""Factory for the observability module (PRD Step 9/10 monitoring).

Matches the uniform per-module factory contract
(``build_agents(bus, logger, settings) -> list[BaseAgent]``) so the observability
collector can be wired into :func:`disastermind.orchestration.loop.build_system`
alongside the tier agents. The single :class:`MetricsCollector` it returns is a
zero-authority Tier-3 observer, so adding it to the DAG is always safe.
"""
from __future__ import annotations

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.config import Settings
from .collector import MetricsCollector


def build_agents(
    bus: MessageBus, logger: DecisionLogger, settings: Settings
) -> list[BaseAgent]:
    """Instantiate the observability agents (PRD Step 9/10).

    Returns a single :class:`MetricsCollector` subscribed to all topics.
    ``settings`` is accepted for interface parity with the other module factories;
    the collector reads no settings directly today.
    """
    return [MetricsCollector(bus, logger)]
