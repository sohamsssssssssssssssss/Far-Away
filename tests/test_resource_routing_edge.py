"""The prediction -> resource -> routing data edge (PRD Steps 4 & 5).

Before the fix the RESOURCE_PLAN payload carried only
{kind, incident_id, module, solver, objective, orders, gaps,
cross_state_order_ids}; it never emitted ``zones``/``demand`` (so routing had no
demand) nor a depot *location* (DeploymentOrder has no coordinates), so the
EvacuationRoutingAgent always returned [] and never published a ROUTING_PLAN.
These tests assert the producer now supplies what the consumer reads.
"""
from __future__ import annotations

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.tier2.resource.agent import ResourceAllocationAgent
from disastermind.tier2.routing.agent import EvacuationRoutingAgent


def _earthquake_prediction(incident_id="usgs:eq") -> Message:
    epi = {"lat": 26.35, "lon": 91.95}
    return Message(
        sender="tier2.prediction.earthquake",
        recipient="tier2.cascade",
        type=MessageType.ALERT,
        priority=Priority.CRITICAL,
        topic=Topic.PREDICTION,
        incident_id=incident_id,
        module=Module.EARTHQUAKE,
        payload={
            "kind": "risk",
            "incident_id": incident_id,
            "module": "B",
            "risk_cells": [
                {
                    "cell_id": "rc-eq-1",
                    "centroid": epi,
                    "probability": 0.7,
                    "horizon_minutes": 0,
                    "population_at_risk": 240,
                }
            ],
            "buildings": [],
            "fire_fronts": [],
        },
    )


def test_resource_plan_carries_zones_and_depots():
    """RESOURCE_PLAN must now expose demand ``zones`` and located ``depots``."""
    agent = ResourceAllocationAgent(InMemoryBus(), DecisionLogger.null(), Settings())
    out = agent.handle(_earthquake_prediction())
    assert len(out) == 1
    payload = out[0].payload
    assert payload["kind"] == "resource_plan"
    assert payload["orders"], "no deployment orders produced"

    zones = payload.get("zones")
    assert zones, "resource plan emitted no demand zones (dead routing edge)"
    z = zones[0]
    assert "zone_id" in z and "centroid" in z and "population" in z

    depots = payload.get("depots")
    assert depots, "resource plan emitted no depots"
    d = depots[0]
    assert d.get("depot") is not None, "depot has no location -> VRP cannot place it"
    assert "lat" in d["depot"] and "lon" in d["depot"]


def test_resource_plan_drives_routing_to_emit_a_plan():
    """End-to-end on two agents: a resource plan must yield a ROUTING_PLAN."""
    res_bus = InMemoryBus()
    resource = ResourceAllocationAgent(res_bus, DecisionLogger.null(), Settings())
    resource_msgs = resource.handle(_earthquake_prediction())
    assert resource_msgs

    routing = EvacuationRoutingAgent(InMemoryBus(), DecisionLogger.null(), Settings())
    routing_out = routing.handle(resource_msgs[0])
    assert routing_out, "routing produced no ROUTING_PLAN from the resource plan"
    plan = routing_out[0]
    assert plan.topic == Topic.ROUTING_PLAN
    routes = plan.payload["routes"]
    assert routes, "routing plan has zero routes"
    # Every route should terminate at a shelter (default or supplied).
    assert all("waypoints" in r for r in routes)


def test_routing_still_prefers_explicit_shelter_inventory():
    """An explicit ``shelters`` list on the plan overrides the default fallback."""
    routing = EvacuationRoutingAgent(InMemoryBus(), DecisionLogger.null(), Settings())
    payload = {
        "kind": "resource_plan",
        "incident_id": "inc-1",
        "module": "B",
        "orders": [
            {"order_id": "o1", "asset_id": "BUS-1", "target_cell": "z1", "priority": 2}
        ],
        "zones": [
            {
                "zone_id": "z1",
                "cell_id": "z1",
                "centroid": {"lat": 26.35, "lon": 91.95},
                "population": 80,
            }
        ],
        "depots": [
            {"vehicle_id": "BUS-1", "depot": {"lat": 26.30, "lon": 91.90}, "capacity": 50}
        ],
        "shelters": [
            {
                "shelter_id": "SH-EXPLICIT",
                "location": {"lat": 26.40, "lon": 91.99},
                "capacity": 500,
                "occupancy": 0,
            }
        ],
    }
    msg = Message(
        sender="resource.allocator",
        recipient="broadcast",
        type=MessageType.INSTRUCTION,
        priority=Priority.HIGH,
        topic=Topic.RESOURCE_PLAN,
        incident_id="inc-1",
        module=Module.EARTHQUAKE,
        payload=payload,
    )
    out = routing.handle(msg)
    assert out
    routes = out[0].payload["routes"]
    assert routes
    assert any(r["shelter_id"] == "SH-EXPLICIT" for r in routes)
