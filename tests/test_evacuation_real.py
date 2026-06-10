"""Evacuation-plan validation on REAL OSM infrastructure (Puri, Fani zone).

Offline, against the committed real OpenStreetMap fixture. Asserts the real road
graph builds, the system's routing reaches real shelters over the real network,
and the real-road detour over straight-line is quantified (the concrete value of
road-aware routing). Small sample keeps it fast and deterministic.
"""
from __future__ import annotations

from disastermind.hindcast.evacuation import (
    build_graph,
    load_puri_osm,
    validate_evacuation,
)


def test_fixture_is_real_osm():
    fx = load_puri_osm()
    assert "OpenStreetMap" in fx["source"]
    assert len(fx["roads"]) > 1000  # the real, dense Puri road network
    assert any(s.get("name") == "Jagannath Temple" for s in fx["shelters"])  # real landmark


def test_real_road_graph_builds_connected():
    g = build_graph(load_puri_osm())
    assert len(g.nodes) > 5000  # thousands of real junctions
    # most junctions have neighbours (a real network, not isolated points)
    degree = sum(1 for k in g.nodes if any(True for _ in g.neighbors(k)))
    assert degree > 0.8 * len(g.nodes)


def test_routing_reaches_real_shelters_on_real_roads():
    v = validate_evacuation(sample_size=12)
    assert v.reached > 0
    assert v.coverage_pct > 50.0  # most coastal points reach a real shelter
    # routes complete within the lead window at evacuation speed
    assert v.max_evac_minutes <= v.within_lead_hours * 60


def test_real_road_detour_exceeds_straight_line():
    """The headline real-data finding: real roads are longer than straight-line."""
    v = validate_evacuation(sample_size=12)
    assert v.detour_ratio_mean > 1.0  # road distance > straight-line (real geometry)
    assert v.road_km_mean >= v.straight_km_mean
