"""Road-network routing tests (PRD Step 5 — evacuation follows roads).

Pure stdlib, fully offline: every graph is built from the committed Overpass
fixture or an in-line dict, and the lazy :func:`fetch_roads` is exercised only via
an *injected* transport / recorded payload — no socket is ever opened.
"""
from __future__ import annotations

import math

import pytest

from disastermind.models.domain import CascadeFailure
from disastermind.models.geo import LatLon, haversine
from disastermind.roadnet import (
    DRIVABLE_HIGHWAYS,
    NoRouteError,
    RoadGraph,
    build_graph_from_segments,
    close_segments,
    fetch_roads,
    load_fixture,
    open_segments,
    parse_geojson,
    parse_overpass,
    reroute_around,
    road_distance,
)
from disastermind.roadnet.graph import node_key

# Fixture junctions (mini_roads.overpass.json).
N1 = LatLon(19.8000, 85.8000)  # junction 1
N3 = LatLon(19.8000, 85.8200)  # junction 3 (E end of Coastal Road)
N5 = LatLon(19.8100, 85.8200)  # junction 5 (detour corner)

# Coastal Road (way 1001) is the short 2-hop route N1->N2->N3.
COASTAL = ["way/1001:0", "way/1001:1"]


@pytest.fixture
def graph() -> RoadGraph:
    return load_fixture()


# ----------------------------------------------------------------- graph basics
def test_fixture_shapes_a_connected_graph(graph: RoadGraph):
    assert len(graph) == 5
    assert graph.segment_ids() >= set(COASTAL)
    # every junction has at least one open outgoing edge
    for k in graph.nodes:
        assert any(True for _ in graph.neighbors(k))


def test_add_edge_is_bidirectional_and_haversine_weighted():
    g = RoadGraph()
    a, b = LatLon(0.0, 0.0), LatLon(0.0, 0.01)
    seg = g.add_edge(a, b)
    fwd = list(g.neighbors(node_key(a)))
    rev = list(g.neighbors(node_key(b)))
    assert len(fwd) == 1 and len(rev) == 1
    assert fwd[0].segment_id == seg == rev[0].segment_id
    assert fwd[0].length_m == pytest.approx(haversine(a, b))


def test_oneway_edge_is_single_direction():
    # parse_overpass honours oneway=yes
    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0},
            {"type": "node", "id": 2, "lat": 0.0, "lon": 0.01},
            {
                "type": "way",
                "id": 9,
                "nodes": [1, 2],
                "tags": {"highway": "primary", "oneway": "yes"},
            },
        ]
    }
    g = parse_overpass(data)
    assert list(g.neighbors(node_key(LatLon(0.0, 0.0))))  # forward exists
    assert not list(g.neighbors(node_key(LatLon(0.0, 0.01))))  # no reverse


# ------------------------------------------------------------- shortest path
def test_shortest_path_picks_shorter_multihop_route(graph: RoadGraph):
    path, dist = graph.shortest_path(N1, N3)
    # short route is the 2-hop Coastal Road: 3 waypoints (N1, N2, N3)
    assert len(path) == 3
    assert path[0] == N1 and path[-1] == N3
    # cheaper than the 3-hop perimeter detour
    detour = road_distance(graph, N1, N5) + haversine(N5, N3)
    assert dist < detour


def test_route_latlons_returns_waypoints(graph: RoadGraph):
    pts = graph.route_latlons(N1, N3)
    assert pts[0] == N1 and pts[-1] == N3
    assert all(isinstance(p, LatLon) for p in pts)


def test_astar_matches_dijkstra_distance(graph: RoadGraph):
    _, d_astar = graph.shortest_path(N1, N5, heuristic=True)
    _, d_dijkstra = graph.shortest_path(N1, N5, heuristic=False)
    assert d_astar == pytest.approx(d_dijkstra)
    assert d_astar > 0.0


def test_astar_heuristic_is_admissible_lower_bound(graph: RoadGraph):
    # straight-line never exceeds the road distance (admissibility)
    _, road = graph.shortest_path(N1, N5)
    assert haversine(N1, N5) <= road + 1e-6


def test_same_node_is_zero_distance(graph: RoadGraph):
    path, dist = graph.shortest_path(N1, N1)
    assert path == [N1] and dist == 0.0


def test_unknown_endpoint_raises(graph: RoadGraph):
    with pytest.raises(NoRouteError):
        graph.shortest_path(LatLon(10.0, 10.0), N3)


# --------------------------------------------------------------- closures
def test_closing_edge_forces_longer_detour(graph: RoadGraph):
    _, direct = graph.shortest_path(N1, N3)
    closed = close_segments(graph, COASTAL)
    assert set(closed) == set(COASTAL)
    detour_path, detour = graph.shortest_path(N1, N3)
    assert detour > direct
    # detour must route via the northern perimeter (junction 5)
    assert N5 in detour_path
    # and must not traverse the closed Coastal Road junctions count
    assert len(detour_path) > 3


def test_reopening_segment_restores_short_route(graph: RoadGraph):
    close_segments(graph, COASTAL)
    open_segments(graph, COASTAL)
    path, _ = graph.shortest_path(N1, N3)
    assert len(path) == 3  # back to the direct Coastal Road


def test_close_consumes_cascade_failure_objects(graph: RoadGraph):
    _, direct = graph.shortest_path(N1, N3)
    failures = [
        CascadeFailure(
            segment_id=sid,
            fails_at_minute=10,
            reason="inundation",
            viable_until_minute=10,
        )
        for sid in COASTAL
    ]
    closed = close_segments(graph, failures)
    assert set(closed) == set(COASTAL)
    assert graph.closed_segments() >= set(COASTAL)
    _, detour = graph.shortest_path(N1, N3)
    assert detour > direct


def test_close_ignores_unknown_segments(graph: RoadGraph):
    assert close_segments(graph, ["no/such/segment"]) == []


def test_close_accepts_dict_payloads(graph: RoadGraph):
    closed = close_segments(graph, [{"segment_id": COASTAL[0]}])
    assert closed == [COASTAL[0]]


def test_reroute_around_helper(graph: RoadGraph):
    path, dist = reroute_around(graph, N1, N3, COASTAL)
    assert N5 in path  # detoured
    assert dist > haversine(N1, N3)


def test_fully_severed_network_has_no_route():
    # a single bridge segment; closing it disconnects the two halves
    g = build_graph_from_segments(
        [{"a": (0.0, 0.0), "b": (0.0, 0.01), "segment_id": "bridge"}]
    )
    close_segments(g, ["bridge"])
    with pytest.raises(NoRouteError):
        g.shortest_path(LatLon(0.0, 0.0), LatLon(0.0, 0.01))


# ---------------------------------------------------------- parse_overpass
def test_parse_overpass_shapes_graph_from_ways():
    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 1.0, "lon": 1.0},
            {"type": "node", "id": 2, "lat": 1.0, "lon": 1.01},
            {"type": "node", "id": 3, "lat": 1.0, "lon": 1.02},
            {
                "type": "way",
                "id": 7,
                "nodes": [1, 2, 3],
                "tags": {"highway": "primary", "name": "Main St"},
            },
        ]
    }
    g = parse_overpass(data)
    assert len(g) == 3
    # a 3-node way -> 2 consecutive segments
    assert g.segment_ids() == {"way/7:0", "way/7:1"}
    edge = next(g.neighbors(node_key(LatLon(1.0, 1.0))))
    assert edge.name == "Main St"


def test_parse_overpass_accepts_raw_json_string():
    g = parse_overpass(
        '{"elements": ['
        '{"type":"node","id":1,"lat":0.0,"lon":0.0},'
        '{"type":"node","id":2,"lat":0.0,"lon":0.01},'
        '{"type":"way","id":3,"nodes":[1,2],"tags":{"highway":"residential"}}]}'
    )
    assert len(g) == 2


def test_parse_overpass_skips_non_drivable_highways():
    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0},
            {"type": "node", "id": 2, "lat": 0.0, "lon": 0.01},
            {
                "type": "way",
                "id": 1,
                "nodes": [1, 2],
                "tags": {"highway": "footway"},
            },
        ]
    }
    assert len(parse_overpass(data)) == 0  # footway dropped
    assert "footway" not in DRIVABLE_HIGHWAYS


def test_parse_overpass_skips_segments_with_missing_nodes():
    # node 2 referenced by the way but not present (clipped at bbox edge)
    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0},
            {
                "type": "way",
                "id": 1,
                "nodes": [1, 2],
                "tags": {"highway": "primary"},
            },
        ]
    }
    g = parse_overpass(data)
    assert g.segment_ids() == set()  # nothing buildable


# ------------------------------------------------------------- parse_geojson
def test_parse_geojson_linestring():
    gj = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Evac Rd"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[85.8, 19.8], [85.81, 19.8], [85.82, 19.8]],
                },
            }
        ],
    }
    g = parse_geojson(gj)
    assert len(g) == 3
    path, dist = g.shortest_path(LatLon(19.8, 85.8), LatLon(19.8, 85.82))
    assert len(path) == 3
    assert dist == pytest.approx(haversine(LatLon(19.8, 85.8), LatLon(19.8, 85.82)))


# ----------------------------------------------------- fetch_roads (offline)
def test_fetch_roads_with_recorded_raw_payload():
    # no transport, no network: a recorded Overpass payload is parsed directly
    raw = {
        "elements": [
            {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0},
            {"type": "node", "id": 2, "lat": 0.0, "lon": 0.01},
            {
                "type": "way",
                "id": 1,
                "nodes": [1, 2],
                "tags": {"highway": "primary"},
            },
        ]
    }
    g = fetch_roads((0.0, 0.0, 0.1, 0.1), raw=raw)
    assert len(g) == 2


def test_fetch_roads_uses_injected_transport_not_network():
    captured = {}

    def fake_transport(url: str, timeout: float):
        captured["url"] = url
        body = (
            '{"elements": ['
            '{"type":"node","id":1,"lat":0.0,"lon":0.0},'
            '{"type":"node","id":2,"lat":0.0,"lon":0.01},'
            '{"type":"way","id":1,"nodes":[1,2],"tags":{"highway":"secondary"}}]}'
        )
        return 200, body

    g = fetch_roads((1.0, 2.0, 3.0, 4.0), transport=fake_transport)
    assert len(g) == 2
    # query carries the bbox and an Overpass [out:json] header
    assert "1.0,2.0,3.0,4.0" in captured["url"]
    assert "out:json" in captured["url"]


def test_fetch_roads_transport_error_propagates():
    def boom(url: str, timeout: float):
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        fetch_roads((0.0, 0.0, 1.0, 1.0), transport=boom)


# ---------------------------------------------------------- road_distance API
def test_road_distance_is_haversine_drop_in(graph: RoadGraph):
    d = road_distance(graph, N1, N3)
    assert isinstance(d, float)
    assert d >= haversine(N1, N3) - 1e-6
    # signature mirrors haversine(a, b) -> float
    assert math.isfinite(d)
