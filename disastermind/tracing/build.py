"""Factory for the tracing module (PRD Step 9/10 observability).

Matches the uniform per-module factory contract
(``build_agents(bus, logger, settings) -> list[BaseAgent]``) so the
:class:`~disastermind.tracing.collector.TraceCollector` *could* be wired into the
DAG alongside the tier agents. Per the package brief we deliberately do **not**
auto-wire it into :func:`disastermind.orchestration.loop.build_system` — callers
opt in by invoking this factory (or constructing the collector directly) so the
load-bearing chain is never perturbed by tracing (PRD Step 2 / Step 10).

The single :class:`TraceCollector` it returns is a zero-authority Tier-3 observer
subscribed to all topics, so adding it to any bus is always safe.
"""
from __future__ import annotations

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.config import Settings
from .collector import TraceCollector


def build_agents(
    bus: MessageBus, logger: DecisionLogger, settings: Settings
) -> list[BaseAgent]:
    """Instantiate the tracing agents (PRD Step 9/10).

    Returns a single :class:`TraceCollector` subscribed to all topics. ``settings``
    is accepted for interface parity with the other module factories; the trace
    collector reads no settings directly today.
    """
    return [TraceCollector(bus, logger)]
