"""Factory for the Tier 2 Field Coordination module (PRD Step 6).

The orchestration layer calls :func:`build_agents` to instantiate every agent
this module owns. Field coordination is a single agent, but the factory returns
a list to match the uniform module contract.
"""
from __future__ import annotations

from .agent import FieldCoordinationAgent


def build_agents(bus, logger, settings) -> list:
    """Instantiate and return the field-coordination agents (list[BaseAgent]).

    :param bus: a :class:`~disastermind.core.bus.MessageBus` implementation.
    :param logger: a :class:`~disastermind.audit.decision_log.DecisionLogger`.
    :param settings: a :class:`~disastermind.core.config.Settings` instance.
    """
    return [FieldCoordinationAgent(bus=bus, logger=logger, settings=settings)]
