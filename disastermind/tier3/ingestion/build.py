"""Factory for the Tier 3 feed-ingestion module (PRD Step 2).

The orchestration layer calls :func:`build_agents` to instantiate every feed
adapter in this module. Ingestion agents are pure edge producers (no decision
authority): they poll external public hazard feeds, normalise observations, and
emit on :data:`~disastermind.core.contracts.Topic.RAW_FEED` from their
``tick()``. The prediction tier (Tier 2) interprets the raw signal.

We construct the full India-centric feed roster across all three hazard families:

  * Seismic (Module B): USGS GeoJSON + NCS (India) RSS.
  * Hydro-meteorological (Module A): CWC India-WRIS gauges, IMD bulletins,
    ISRO Bhuvan inundation footprints, Open-Meteo hourly forecast.
  * Fire spread (Module C): NASA FIRMS active-fire detections + OpenWeatherMap
    wind speed/direction.

All adapters default to ``live=False`` so they use their offline ``sample()``
fixtures — the package imports and the test-suite runs with stdlib only and no
network calls (PRD Step 10, graceful degradation). Set ``DM_FEEDS_LIVE=1`` to
enable real network fetches.
"""
from __future__ import annotations

import os

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.config import Settings
from .hydromet import BhuvanFeedAgent, CWCFeedAgent, IMDFeedAgent
from .openmeteo import OpenMeteoFeedAgent
from .seismic import NCSFeedAgent, USGSFeedAgent
from .wildfire import FIRMSFeedAgent, OpenWeatherMapFeedAgent


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
) -> list[BaseAgent]:
    """Instantiate and return all feed-ingestion agents (PRD Step 2).

    Each adapter subscribes to nothing inbound (pure producer) and publishes
    RAW_FEED from its ``tick()``. ``settings`` is threaded through so the live
    fetch path can read provider URLs/keys; the default degraded path ignores it.
    """
    live = os.environ.get("DM_FEEDS_LIVE", "").lower() in {"1", "true", "yes", "on"}
    return [
        USGSFeedAgent(bus=bus, logger=logger, settings=settings, live=live),
        NCSFeedAgent(bus=bus, logger=logger, settings=settings, live=live),
        CWCFeedAgent(bus=bus, logger=logger, settings=settings, live=live),
        IMDFeedAgent(bus=bus, logger=logger, settings=settings, live=live),
        BhuvanFeedAgent(bus=bus, logger=logger, settings=settings, live=live),
        OpenMeteoFeedAgent(bus=bus, logger=logger, settings=settings, live=live),
        FIRMSFeedAgent(bus=bus, logger=logger, settings=settings, live=live),
        OpenWeatherMapFeedAgent(bus=bus, logger=logger, settings=settings, live=live),
    ]
