"""Module C — synthetic urban fire / collapse scenario generator (PRD Step 1-7).

A high-intensity fire breaks out in a dense market block with a strong dry
gale fanning the front toward a hospital. We inject a synthetic URBAN_FIRE
:class:`DisasterEvent` on RAW_FEED, which the fire-spread forecaster turns into
a cellular-automata fire perimeter (``fire_fronts``) threatening critical
infrastructure.

The current build's fire forecaster emits ``fire_fronts`` rather than
rescue-zone ``risk_cells``, and the Tier 2 resource allocator builds deployment
demand from ``risk_cells``. So this scenario *also* surfaces the fire's
rescue-priority zones as a synthetic PREDICTION so the
resource -> routing -> field -> dispatch chain fires (PRD Step 4-7):

    RAW_FEED -> PREDICTION (fire perimeter at T+15/30/60 min)
    PREDICTION(risk) -> RESOURCE_PLAN (fire engine / USAR / medical allocation)
             -> ROUTING_PLAN -> FIELD_ORDER -> DISPATCH

By default the chain dispatches autonomously. ``escalate=True`` instead injects a
requisition of private water-tanker infrastructure so the Commander escalates to
a human (PRD Step 7, ``REQUISITION_PRIVATE``).
"""
from __future__ import annotations

from ..core.contracts import EscalationTrigger, Module, Priority
from ..orchestration.loop import CoordinationLoop
from .base import (
    SCENARIO_GENERATORS,
    build_loop,
    inject_escalation_order,
    inject_raw_event,
    inject_rescue_prediction,
    seed_field_teams,
)

INCIDENT_ID = "scenario:fire-market-block-delhi"

# Epicentre of the synthetic fire (co-located with the seeded fire engines so
# deployment ETAs are realistic).
_LAT, _LON = 20.30, 85.84


def _rescue_zones() -> list[dict]:
    """Rescue-priority risk cells around the fire front (population at risk)."""
    zones: list[dict] = []
    for i in range(4):
        zones.append(
            {
                "cell_id": f"fire-rescue-zone-{i}",
                "centroid": {"lat": _LAT + 0.001 * i, "lon": _LON + 0.001 * i},
                "probability": 0.82,
                "horizon_minutes": 30,
                "population_at_risk": 450 + 60 * i,
                "shap": {"fire_front_proximity": round(0.82 - 0.05 * i, 4)},
            }
        )
    return zones


def simulate_urban_fire(
    loop: CoordinationLoop | None = None,
    *,
    escalate: bool = False,
    drive_cycles: int = 1,
) -> CoordinationLoop:
    """Build (or reuse) the DAG, inject a fire signal, drive the loop.

    Returns the driven :class:`CoordinationLoop` (inspect ``loop.bus.history``).
    """
    loop = loop or build_loop()
    seed_field_teams(loop.bus)

    # FIRMS-style hot, high-confidence detection + gale-force dry wind fanning a
    # market-block fire toward a hospital (PRD Step 1, Module C activation).
    inject_raw_event(
        loop.bus,
        kind="urban_fire",
        module=Module.FIRE_COLLAPSE,
        incident_id=INCIDENT_ID,
        lat=_LAT,
        lon=_LON,
        severity=2.6,
        meta={
            "brightness_k": 364.5,
            "wind_speed_ms": 16.0,
            "wind_dir_deg": 245.0,
            "critical_infrastructure": [
                {"name": "district_hospital", "location": {"lat": _LAT + 0.0015, "lon": _LON + 0.0015}},
                {"name": "lpg_godown", "location": {"lat": _LAT + 0.0008, "lon": _LON + 0.0006}},
            ],
        },
        priority=Priority.CRITICAL,
        reasoning=[
            "FIRMS 364K active-fire pixel + 16 m/s dry gale (PRD Step 1, Module C)",
            "fire front projected onto district hospital within T+30 min",
        ],
    )

    # Surface the fire's rescue-priority zones so the resource allocator can
    # build deployment demand (the fire forecaster emits fire_fronts, not the
    # rescue risk_cells the allocator consumes).
    inject_rescue_prediction(
        loop.bus,
        module=Module.FIRE_COLLAPSE,
        incident_id=INCIDENT_ID,
        risk_cells=_rescue_zones(),
        reasoning=[
            "rescue-priority zones derived from projected fire perimeter",
            "evacuate market block + hospital approach before front arrival",
        ],
    )

    if escalate:
        # Requisitioning private water-tanker / cold-storage infrastructure to
        # fight the fire exceeds autonomous authority (PRD Step 7).
        inject_escalation_order(
            loop.bus,
            module=Module.FIRE_COLLAPSE,
            incident_id=INCIDENT_ID,
            trigger=EscalationTrigger.REQUISITION_PRIVATE,
            team_id="FIRE-01",
            site="private water-tanker depot adjacent to market block",
            reason="requisition private water tankers + cold-storage ammonia shutoff",
            summary="requisition private water-tanker infrastructure to fight the fire",
            scale=1,
        )

    loop.run(max_cycles=max(1, drive_cycles), clock=lambda: 0.0, sleep=lambda _s: None)
    return loop


SCENARIO_GENERATORS["C"] = simulate_urban_fire
