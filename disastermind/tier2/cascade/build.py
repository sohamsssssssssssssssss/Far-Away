"""Factory for the Tier 2 Cascade-Prediction module (PRD Step 3).

The orchestration layer calls :func:`build_agents` to instantiate every cascade
specialist this module owns. The cascade DAG node sits on the
``prediction -> cascade -> resource`` edge: it subscribes to
:data:`~disastermind.core.contracts.Topic.PREDICTION` and publishes
:data:`~disastermind.core.contracts.Topic.CASCADE`.

Cascade is hazard-specific, so we ship one specialist per family:

  * :class:`~disastermind.tier2.cascade.flood.FloodCascadeAgent` — Module A:
    inundation-driven road/bridge cutoff (which rescue routes flood out before
    teams can return).
  * :class:`~disastermind.tier2.cascade.aftershock.EarthquakeCascadeAgent` —
    Module B: aftershock-driven structural cascade (Omori-Utsu forecast of a
    damaging M5.0+ aftershock finishing off already-weakened buildings and
    cutting their access corridors).

Each agent filters on ``message.module`` so a single shared bus fans the right
predictions to the right specialist. Both are stdlib-only (PRD HARD RULE 3).
"""
from __future__ import annotations

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.config import Settings
from .aftershock import EarthquakeCascadeAgent
from .flood import FloodCascadeAgent


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
) -> list[BaseAgent]:
    """Instantiate and return all cascade-module agents (PRD Step 3).

    Each subscribes to PREDICTION and publishes CASCADE; the flood specialist
    handles Module A, the earthquake specialist handles Module B.
    """
    return [
        FloodCascadeAgent(bus=bus, logger=logger, settings=settings),
        EarthquakeCascadeAgent(bus=bus, logger=logger, settings=settings),
    ]
