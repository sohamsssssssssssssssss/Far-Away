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

import os

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.config import Settings
from .persistor import StatePersistor


def _persist_live() -> bool:
    """True when durable backends are requested via ``DM_PERSIST`` (or ``DM_LIVE``).

    Default is OFF (in-memory) so the test-suite and an unconfigured deployment
    never touch a database. On Railway, add a Postgres/Timescale/ES plugin, set
    the ``DM_*_DSN`` vars, and ``DM_PERSIST=1`` to make state durable.
    """
    for key in ("DM_PERSIST", "DM_LIVE"):
        val = os.environ.get(key)
        if val is not None and val.strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
) -> list[BaseAgent]:
    """Instantiate the state-persistence agents (PRD Step 9).

    Returns a single :class:`StatePersistor`. Storage is **durable** —
    ``Storage.from_settings(settings, live=True)`` writing telemetry to
    TimescaleDB, the audit trail to Elasticsearch and resource state to PostGIS —
    when ``DM_PERSIST``/``DM_LIVE`` is set; otherwise an offline in-memory store
    (each live repo still degrades to in-memory on its own if its backend is
    unreachable, so this never hard-fails). State then survives process restarts.
    """
    from ..storage import Storage

    storage = Storage.from_settings(settings, live=True) if _persist_live() else Storage.in_memory()
    return [StatePersistor(bus=bus, logger=logger, storage=storage)]
