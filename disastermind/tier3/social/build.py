"""Factory for the Tier 3 social-media NLP module (PRD Step 1 Module C / Step 2).

The orchestration layer calls :func:`build_agents` to instantiate the social
NLP agent. Like the other Tier 3 edge producers it has no decision authority:
it ingests geo-tagged posts, scores them against the collapse/disaster lexicon,
clusters them by geo bucket within a time window, and emits on
:data:`~disastermind.core.contracts.Topic.RAW_FEED` from its ``tick()``. The
prediction tier (Tier 2) interprets the signal.

The agent defaults to ``live=False`` so it uses its offline ``sample()`` fixture
— the package imports and the test-suite runs with stdlib only and no network
(PRD Step 10, graceful degradation). Set ``DM_SOCIAL_LIVE=1`` to enable real
fetches (the live path itself still degrades to ``sample()`` on any failure).
"""
from __future__ import annotations

import os

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.config import Settings
from .agent import SocialNLPAgent


def build_agents(
    bus: MessageBus,
    logger: DecisionLogger,
    settings: Settings,
) -> list[BaseAgent]:
    """Instantiate and return the social-media NLP agent(s) (PRD Step 2).

    The agent subscribes to nothing inbound (pure edge producer) and publishes
    RAW_FEED alerts from its ``tick()`` whenever a geo-temporal collapse keyword
    cluster clears the activation threshold.
    """
    live = os.environ.get("DM_SOCIAL_LIVE", "").lower() in {"1", "true", "yes", "on"}
    return [
        SocialNLPAgent(bus=bus, logger=logger, settings=settings, live=live),
    ]
