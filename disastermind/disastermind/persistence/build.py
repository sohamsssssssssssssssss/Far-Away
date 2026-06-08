"""Factory for the state-persistence module (PRD Step 9 — Decision Logging & State).

Matches the uniform per-module factory contract
(``build_agents(bus, logger, settings) -> list[BaseAgent]``) so the
:class:`~disastermind.persistence.persistor.StatePersistor` can be wired into the
DAG alongside the tier agents. The single persistor it returns is a Tier-3
zero-authority observer that subscribes to every topic and emits nothing, so
adding it to the system is always safe (PRD Step 10).

Storage defaults to :meth:`~disastermind.storage.Storage.in_memory` — fully
offline, no network — so the persistor wires in cleanly during tests. To write
through real backends, build a live :class:`~disastermind.storage.Storage`
(``Storage.from_settings(settings, live=True)``) and construct the persistor
directly; each repo still degrades to its own in-memory fallback if unreachable.
"""
from __future__ import annotations

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.config import Settings
from .persistor import StatePersistor


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
) -> list[BaseAgent]:
    """Instantiate the state-persistence agents (PRD Step 9).

    Returns a single :class:`StatePersistor` backed by an offline in-memory
    :class:`~disastermind.storage.Storage`. ``settings`` is accepted for
    interface parity with the other module factories.
    """
    return [StatePersistor(bus=bus, logger=logger)]
