"""Concurrent multi-incident orchestration (PRD: "one Commander Agent per
active disaster").

DisasterMind's PRD calls for *one Commander Agent per active disaster* so that
several disasters can be coordinated at once without their decision chains
bleeding into each other. This package adds that capability **on top of** the
frozen single-incident stack without touching it:

    from disastermind.multi_incident import IncidentManager, IncidentSeed
    mgr = IncidentManager()
    mgr.activate("eq-cuttack", IncidentSeed.earthquake(20.30, 85.84, magnitude=6.2))
    mgr.activate("flood-mahanadi", IncidentSeed.flood(20.30, 85.84, river_level_m=6.5))
    mgr.run_cycle()
    snap = mgr.snapshot()

Isolation model (documented design choice)
------------------------------------------
The **simplest correct** isolation is *one bus + one fully-wired agent DAG per
incident*. Each incident gets its own :class:`~disastermind.core.bus.InMemoryBus`
and its own :class:`~disastermind.orchestration.loop.CoordinationLoop` built by
:func:`disastermind.orchestration.build.build_system`. Because every incident's
agents publish/subscribe on a *private* bus, there is structurally **zero**
cross-talk between incidents — no shared mutable state, no topic collisions, no
need to thread ``incident_id`` filtering through every agent. A failed incident
DAG cannot corrupt another's state (PRD Step 10 graceful degradation). The cost
(N independent agent graphs) is acceptable for the handful of simultaneously
active disasters the PRD anticipates, and it reuses the *exact* proven wiring the
single-incident system already trusts.

Stdlib-only, offline, deterministic (PRD HARD RULE 2): no network at import or in
any test path; the heavy optional deps stay lazily imported inside the frozen
stack we delegate to.
"""
from __future__ import annotations

from .manager import (
    IncidentManager,
    IncidentRuntime,
    IncidentSeed,
    IncidentSnapshot,
    MultiIncidentSnapshot,
)

__all__ = [
    "IncidentManager",
    "IncidentRuntime",
    "IncidentSeed",
    "IncidentSnapshot",
    "MultiIncidentSnapshot",
]
