"""Synthetic disaster scenario generators (PRD Group A, Step 10 demo harness).

Each generator builds the full DisasterMind agent DAG on an in-memory bus,
seeds field teams via a GPS-beacon telemetry frame, injects a *realistic*
synthetic hazard signal for one module, and drives the coordination loop one
cycle so the load-bearing chain

    RAW_FEED -> PREDICTION -> CASCADE -> RESOURCE_PLAN -> ROUTING_PLAN
             -> FIELD_ORDER -> DISPATCH / ESCALATION

fires end-to-end. Every generator returns the driven
:class:`~disastermind.orchestration.loop.CoordinationLoop` so the CLI (and
tests) can inspect ``loop.bus.history`` for the resulting DISPATCH/ESCALATION
messages and topic counts.

Stdlib-only and offline by construction (PRD HARD RULE 2 / Step 10 graceful
degradation): no network, no heavy dependency, fully deterministic.
"""
from __future__ import annotations

from .base import (
    SCENARIO_GENERATORS,
    ScenarioResult,
    run_scenario,
    summarise_loop,
)
from .cyclone_flood import simulate_cyclone_flood
from .earthquake import simulate_earthquake
from .urban_fire import simulate_urban_fire

__all__ = [
    "simulate_cyclone_flood",
    "simulate_earthquake",
    "simulate_urban_fire",
    "run_scenario",
    "summarise_loop",
    "ScenarioResult",
    "SCENARIO_GENERATORS",
]
