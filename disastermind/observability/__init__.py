"""Observability module (PRD Step 9/10): metrics, exposition, and health probe.

Provides a read-only telemetry plane for DisasterMind:
  * :class:`MetricsCollector` — a zero-authority Tier-3 agent that subscribes to
    every topic and tallies the message stream.
  * :func:`render` — Prometheus-style text exposition (no external dependency).
  * :func:`health` — component-liveness report over a built coordination loop.

Wire it into the DAG via :func:`build_agents`.
"""
from __future__ import annotations

from .build import build_agents
from .collector import DEFAULT_LATENCY_BUCKETS, MetricsCollector, all_topics
from .exposition import render
from .health import health

__all__ = [
    "build_agents",
    "MetricsCollector",
    "all_topics",
    "render",
    "health",
    "DEFAULT_LATENCY_BUCKETS",
]
