"""Real road-network routing for evacuation planning (PRD Step 5).

Evacuation routes should follow the *road network* (OpenStreetMap / Overpass),
not straight haversine lines. This package is a self-contained, pure-stdlib
routing layer:

  * :mod:`~disastermind.roadnet.graph` — :class:`RoadGraph` (LatLon junction
    nodes, length-weighted edges), Dijkstra / A\\* :meth:`shortest_path`
    (stdlib :mod:`heapq`), :func:`road_distance` (a haversine drop-in that
    follows roads), and :func:`route_latlons`.
  * :mod:`~disastermind.roadnet.overpass` — :func:`parse_overpass` /
    :func:`parse_geojson` (pure parsers), :func:`load_fixture` (a committed
    offline road network), and a lazy :func:`fetch_roads` that uses the shared
    HTTP transport seam (real Overpass in prod, injected fixture in tests).
  * :mod:`~disastermind.roadnet.closures` — consume ``CascadeFailure``-style
    segment closures and re-route around inundated / collapsed / burning roads.

Importing this package is inert: no network, no bus wiring. It is a *new* package
and does **not** modify :mod:`disastermind.tier2.routing`; an evac router can opt
in by swapping :func:`~disastermind.models.geo.haversine` for
:func:`road_distance`.
"""
from __future__ import annotations

from .closures import close_segments, open_segments, reroute_around
from .graph import (
    Edge,
    NoRouteError,
    RoadGraph,
    build_graph_from_segments,
    node_key,
    road_distance,
)
from .overpass import (
    DRIVABLE_HIGHWAYS,
    FIXTURE_PATH,
    OVERPASS_URL,
    fetch_roads,
    load_fixture,
    overpass_query,
    parse_geojson,
    parse_overpass,
)

__all__ = [
    "Edge",
    "RoadGraph",
    "NoRouteError",
    "node_key",
    "road_distance",
    "build_graph_from_segments",
    "close_segments",
    "open_segments",
    "reroute_around",
    "parse_overpass",
    "parse_geojson",
    "load_fixture",
    "fetch_roads",
    "overpass_query",
    "DRIVABLE_HIGHWAYS",
    "OVERPASS_URL",
    "FIXTURE_PATH",
]
