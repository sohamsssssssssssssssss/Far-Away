"""Cascade-prediction module (PRD Step 3).

The cascade DAG node sits on prediction -> cascade -> resource. Before the fix
the only cascade agent filtered out Module.EARTHQUAKE entirely, so an earthquake
prediction produced NO Topic.CASCADE message and omori.py (the Omori-Utsu
aftershock model) was never used. These tests assert that both hazard families
now produce cascade output via the shared factory.
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
from disastermind.tier2.cascade import build as cascade_build
from disastermind.tier2.cascade.aftershock import EarthquakeCascadeAgent
from disastermind.tier2.cascade.omori import fit_params, probability_by_horizon


def _build():
    return cascade_build.build_agents(InMemoryBus(), DecisionLogger.null(), Settings())


def test_cascade_factory_builds_both_specialists():
    agents = _build()
    names = {a.name for a in agents}
    assert "flood_cascade" in names
    assert "earthquake_cascade" in names


def test_omori_probabilities_are_monotonic_and_bounded():
    """Cumulative aftershock probability grows with horizon and stays in [0, 1]."""
    params = fit_params(magnitude=6.5, depth_km=10.0)
    probs = probability_by_horizon(params, m0=5.0, horizons_hours=(24, 48, 72))
    vals = [probs[24], probs[48], probs[72]]
    assert all(0.0 <= v <= 1.0 for v in vals)
    assert vals[0] <= vals[1] <= vals[2]
    # A strong, shallow mainshock should give a non-trivial aftershock chance.
    assert vals[-1] > 0.0


def _earthquake_prediction(incident_id="usgs:test", module=Module.EARTHQUAKE) -> Message:
    return Message(
        sender="tier2.prediction.earthquake",
        recipient="tier2.cascade",
        type=MessageType.ALERT,
        priority=Priority.CRITICAL,
        topic=Topic.PREDICTION,
        incident_id=incident_id,
        module=module,
        payload={
            "kind": "risk",
            "incident_id": incident_id,
            "module": module.value,
            "magnitude": 6.4,
            "depth_km": 12.0,
            "buildings": [
                {
                    "building_id": "b1",
                    "location": {"lat": 26.35, "lon": 91.95},
                    "collapse_probability": 0.72,
                    "estimated_trapped": 4,
                    "construction": "kutcha",
                },
                {
                    "building_id": "b2",
                    "location": {"lat": 26.36, "lon": 91.96},
                    "collapse_probability": 0.10,
                    "estimated_trapped": 0,
                    "construction": "rcc",
                },
            ],
            "risk_cells": [
                {
                    "cell_id": "z1",
                    "centroid": {"lat": 26.35, "lon": 91.95},
                    "probability": 0.55,
                    "horizon_minutes": 0,
                    "population_at_risk": 12,
                }
            ],
            "fire_fronts": [],
        },
    )


def test_earthquake_prediction_produces_cascade():
    """An EARTHQUAKE prediction must yield a Topic.CASCADE message (was 0 before)."""
    agent = EarthquakeCascadeAgent(InMemoryBus(), DecisionLogger.null(), Settings())
    out = agent.handle(_earthquake_prediction())
    assert len(out) == 1
    msg = out[0]
    assert msg.topic == Topic.CASCADE
    assert msg.module is Module.EARTHQUAKE
    failures = msg.payload["failures"]
    assert failures, "no cascade failures projected for a damaging quake"
    # Weakened structures use the earthquake-specific reason.
    assert all(f["reason"] == "high_mmi" for f in failures)
    # The most-damaged building must be flagged as an aftershock cascade risk.
    assert any(f["segment_id"] == "b1" for f in failures)
    # The strongly-resilient RCC building is below threshold and excluded.
    assert all(f["segment_id"] != "b2" for f in failures)
    assert "aftershock_probability" in msg.payload


def test_earthquake_cascade_ignores_flood_module():
    agent = EarthquakeCascadeAgent(InMemoryBus(), DecisionLogger.null(), Settings())
    msg = _earthquake_prediction(module=Module.CYCLONE_FLOOD)
    assert agent.handle(msg) == []


def test_flood_module_still_routed_through_factory_to_flood_agent():
    """The flood specialist still handles Module A inundation cascades."""
    from disastermind.tier2.cascade.flood import FloodCascadeAgent

    agent = FloodCascadeAgent(InMemoryBus(), DecisionLogger.null(), Settings())
    # Seed high-probability inundation cells right on each road-segment midpoint
    # so the haversine influence check reliably flags a failure.
    risk_cells = [
        {
            "cell_id": f"rc-{i}",
            "centroid": {"lat": seg.midpoint().lat, "lon": seg.midpoint().lon},
            "probability": 0.9,
            "horizon_minutes": 20 + 5 * i,
        }
        for i, seg in enumerate(agent.segments)
    ]
    msg = Message(
        sender="tier2.prediction.cyclone",
        recipient="tier2.cascade",
        type=MessageType.ALERT,
        priority=Priority.HIGH,
        topic=Topic.PREDICTION,
        incident_id="imd:test",
        module=Module.CYCLONE_FLOOD,
        payload={"kind": "risk", "risk_cells": risk_cells},
    )
    out = agent.handle(msg)
    assert len(out) == 1
    assert out[0].topic == Topic.CASCADE
    assert out[0].module is Module.CYCLONE_FLOOD
