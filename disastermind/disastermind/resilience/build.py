"""Factory for the resilience module (PRD Step 10 graceful-degradation probe).

Matches the uniform per-module factory contract
(``build_agents(bus, logger, settings) -> list[BaseAgent]``) so a chaos
:class:`~disastermind.resilience.harness.FailingAgent` can be wired into the DAG
to *prove* agent isolation. This factory is intentionally NOT in the default
``MODULE_BUILD_PATHS`` — wiring a deliberately-failing agent into production
boot would be reckless. It exists so tests (and an opt-in chaos run) can inject
the probe through the same uniform contract every other module uses.
"""
from __future__ import annotations

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.config import Settings
from .harness import FailingAgent


def build_agents(
    bus: MessageBus, logger: DecisionLogger, settings: Settings
) -> list[BaseAgent]:
    """Instantiate the resilience module's agents (a single chaos probe).

    ``settings`` is accepted for interface parity with the other module
    factories. Returns one zero-authority :class:`FailingAgent` whose only job is
    to raise — verifying that ``BaseAgent``/``InMemoryBus`` isolate the failure
    and the rest of the DAG keeps running (PRD Step 10).
    """
    return [FailingAgent(bus=bus, logger=logger)]
