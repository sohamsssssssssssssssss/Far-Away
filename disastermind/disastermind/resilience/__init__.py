"""Resilience / graceful-degradation harness (PRD Step 10).

This package does not add new behaviour to the running system; it *proves* the
graceful-degradation claims the foundation already makes (PRD Step 10):

  (a) **Agent isolation** — a Tier-3/2 agent that raises inside ``handle()`` /
      ``tick()`` must NOT take down the bus, the coordination loop, or its
      sibling agents. ``BaseAgent._on_message`` / ``run_tick`` and
      ``InMemoryBus.publish`` swallow subscriber exceptions, so the
      load-bearing chain still reaches ``Topic.DISPATCH``.

  (b) **Module isolation** — ``build_system`` loads every module defensively;
      a module whose import/construction fails is recorded in
      ``loop.degraded_modules`` and skipped, yet the rest of the DAG still
      reaches ``Topic.DISPATCH``.

  (c) **Bus failover** — ``KafkaBus`` pointed at an unreachable broker reports
      ``degraded=True`` and transparently falls back to an in-memory bus (it
      never touches a real broker because ``confluent_kafka`` is imported
      lazily and absent in the test environment).

  (d) **Last-known-state survival** — once the bus stops delivering new
      messages, previously-produced orders remain available in
      ``InMemoryBus.history`` so agents can keep operating on last-known state.

All helpers are stdlib-only and offline by construction (PRD HARD RULE 2).
"""
from __future__ import annotations

from .harness import (
    BROKEN_MODULE_PATH,
    FailingAgent,
    FrozenBus,
    build_with_broken_module,
    degraded_kafka_bus,
    drive_to_dispatch,
    inject_failing_agent,
    last_known_orders,
    seed_field_teams,
)

__all__ = [
    "FailingAgent",
    "FrozenBus",
    "BROKEN_MODULE_PATH",
    "inject_failing_agent",
    "build_with_broken_module",
    "degraded_kafka_bus",
    "drive_to_dispatch",
    "seed_field_teams",
    "last_known_orders",
]
