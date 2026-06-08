"""Factory for the Tier 2 Prediction & Assessment module (PRD Step 3).

The orchestration layer calls :func:`build_agents` to instantiate every agent in
this module. We construct all three domain specialists (cyclone/flood,
earthquake, urban-fire) wired to the shared bus and decision logger.
"""
from __future__ import annotations

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.config import Settings
from .agents import (
    CyclonePredictionAgent,
    EarthquakeImpactAgent,
    FireSpreadAgent,
)


def build_agents(
    bus: MessageBus, logger: DecisionLogger, settings: Settings
) -> list[BaseAgent]:
    """Instantiate the three Tier 2 prediction agents (PRD Step 3).

    Each subscribes to RAW_FEED + IOT_TELEMETRY and publishes PREDICTION.
    ``settings`` is accepted for interface parity with the other modules; the
    prediction agents read no settings directly today.
    """
    return [
        CyclonePredictionAgent(bus, logger),
        EarthquakeImpactAgent(bus, logger),
        FireSpreadAgent(bus, logger),
    ]
