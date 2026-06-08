"""Module A — synthetic cyclone / flood scenario generator (PRD Step 1-7).

A Severe Cyclonic Storm makes landfall on the Odisha coast with a storm surge
and torrential rain; a Brahmaputra/Mahanadi-belt river gauge tops its danger
level. We inject a synthetic FLOOD/CYCLONE :class:`DisasterEvent` on RAW_FEED
which drives:

    RAW_FEED -> PREDICTION (inundation grid + population_at_risk)
             -> CASCADE   (road/bridge cutoff windows)
             -> RESOURCE_PLAN (equity-weighted boat/NDRF allocation)
             -> ROUTING_PLAN -> FIELD_ORDER -> DISPATCH

By default the chain dispatches autonomously. ``escalate=True`` instead injects a
mass-evacuation order (>10 000 people) so the Commander escalates to a human
(PRD Step 7, ``MASS_EVACUATION``).
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

INCIDENT_ID = "scenario:cyclone-aarambh-odisha"


def simulate_cyclone_flood(
    loop: CoordinationLoop | None = None,
    *,
    escalate: bool = False,
    drive_cycles: int = 1,
) -> CoordinationLoop:
    """Build (or reuse) the DAG, inject a cyclone/flood signal, drive the loop.

    Returns the driven :class:`CoordinationLoop` (inspect ``loop.bus.history``).
    """
    loop = loop or build_loop()
    seed_field_teams(loop.bus)

    # Severe Cyclonic Storm "Aarambh": 130 km/h winds, ~3 m surge, 180 mm/24h
    # rain over the Mahanadi delta — comfortably past the Module A thresholds
    # (IMD cyclone alert + gauge >= 75 % of danger level, PRD Step 1).
    inject_raw_event(
        loop.bus,
        kind="flood",
        module=Module.CYCLONE_FLOOD,
        incident_id=INCIDENT_ID,
        lat=20.30,
        lon=85.84,
        severity=3.0,
        meta={
            "system_name": "Cyclone Aarambh",
            "category": "Severe Cyclonic Storm",
            "max_wind_kmph": 130.0,
            "storm_surge_m": 3.0,
            "rainfall_mm": 180.0,
            "river_level_m": 6.5,
            "warning_colour": "red",
            "region": "Mahanadi delta, Odisha",
        },
        observations=[{"population": 1500}],
        priority=Priority.CRITICAL,
        reasoning=[
            "IMD Severe Cyclonic Storm 'Aarambh' alert (PRD Step 1, Module A)",
            "river gauge at 130% of danger level; 180 mm/24h red rainfall warning",
        ],
    )

    if escalate:
        # Mandatory evacuation of >10 000 residents from the surge zone exceeds
        # autonomous authority -> Commander escalates (PRD Step 7).
        inject_escalation_order(
            loop.bus,
            module=Module.CYCLONE_FLOOD,
            incident_id=INCIDENT_ID,
            trigger=EscalationTrigger.MASS_EVACUATION,
            team_id="NDRF-01",
            site="Mahanadi delta low-lying wards",
            reason="mandatory evacuation of 14,000 residents ahead of landfall",
            summary="mandatory evacuation > 10,000 ahead of Cyclone Aarambh landfall",
            scale=14000,
        )

    loop.run(max_cycles=max(1, drive_cycles), clock=lambda: 0.0, sleep=lambda _s: None)
    return loop


SCENARIO_GENERATORS["A"] = simulate_cyclone_flood
