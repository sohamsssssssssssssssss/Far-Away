"""Module B — synthetic earthquake scenario generator (PRD Step 1-7).

A shallow M6.2 strikes a densely-built urban belt. We inject a synthetic
EARTHQUAKE :class:`DisasterEvent` on RAW_FEED which drives:

    RAW_FEED -> PREDICTION (HAZUS-style collapse probability + trapped estimate)
             -> CASCADE   (Omori-Utsu aftershock forecast finishing weak buildings)
             -> RESOURCE_PLAN (USAR/medical/heli allocation to rescue zones)
             -> ROUTING_PLAN -> FIELD_ORDER -> DISPATCH

By default the chain dispatches autonomously. ``escalate=True`` instead injects a
cross-state mutual-aid order so the Commander escalates to a human
(PRD Step 7, ``CROSS_STATE_RESOURCE``).
"""
from __future__ import annotations

from ..core.contracts import EscalationTrigger, Module, Priority
from ..orchestration.loop import CoordinationLoop
from .base import (
    SCENARIO_GENERATORS,
    build_loop,
    inject_escalation_order,
    inject_raw_event,
    seed_field_teams,
)

INCIDENT_ID = "scenario:eq-m6.2-cuttack"


def simulate_earthquake(
    loop: CoordinationLoop | None = None,
    *,
    escalate: bool = False,
    drive_cycles: int = 1,
) -> CoordinationLoop:
    """Build (or reuse) the DAG, inject an earthquake signal, drive the loop.

    Returns the driven :class:`CoordinationLoop` (inspect ``loop.bus.history``).
    """
    loop = loop or build_loop()
    seed_field_teams(loop.bus)

    # Shallow (12 km) M6.2 under a mixed kutcha/pucca/RCC urban belt — well past
    # the Module B M4.5+ activation threshold (PRD Step 1), with shallow depth
    # driving high MMI -> high collapse probability and trapped occupants.
    inject_raw_event(
        loop.bus,
        kind="earthquake",
        module=Module.EARTHQUAKE,
        incident_id=INCIDENT_ID,
        lat=20.30,
        lon=85.84,
        severity=6.2,
        meta={
            "magnitude": 6.2,
            "depth_km": 12.0,
            "place": "Cuttack urban belt, Odisha",
            "tsunami": 0,
        },
        priority=Priority.CRITICAL,
        reasoning=[
            "USGS/NCS M6.2 shallow event (PRD Step 1, Module B: M4.5+ activation)",
            "depth 12 km under dense mixed-construction urban belt -> high MMI",
        ],
    )

    if escalate:
        # Neighbouring-state NDRF battalion needed -> cross-state mutual aid
        # exceeds autonomous authority (PRD Step 7).
        inject_escalation_order(
            loop.bus,
            module=Module.EARTHQUAKE,
            incident_id=INCIDENT_ID,
            trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
            team_id="NDRF-99",
            site="collapsed RCC apartment block, ward 12",
            reason="cross-state mutual aid: neighbouring NDRF battalion required",
            summary="needs a neighbouring state's NDRF battalion for USAR surge",
            scale=1,
        )

    loop.run(max_cycles=max(1, drive_cycles), clock=lambda: 0.0, sleep=lambda _s: None)
    return loop


SCENARIO_GENERATORS["B"] = simulate_earthquake
