"""Parse OpenStreetMap road data into a :class:`RoadGraph` (PRD Step 5).

Two shapes are supported, both pure and stdlib-only:

  * **Overpass JSON** (``out json;`` from the Overpass API) — ``elements`` of
    ``node`` and ``way`` records. :func:`parse_overpass` resolves each ``way``
    into consecutive node-to-node road segments, keyed by ``way/<id>:<i>`` so
    cascade closures can name an individual segment.
  * **GeoJSON** ``LineString`` / ``MultiLineString`` features —
    :func:`parse_geojson` splits each line into per-vertex segments.

:func:`fetch_roads` is the production entry point: it builds an Overpass QL query
for a bounding box and GETs it through the **shared HTTP transport seam**
(:mod:`disastermind.tier3.ingestion.http`). In tests a fixture ``transport`` /
``raw`` is injected, so the suite never touches the network. Importing this module
is inert — no query runs until :func:`fetch_roads` is called.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models.geo import LatLon
from .graph import RoadGraph

# Public Overpass endpoint (only contacted by fetch_roads in production).
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: Highway classes a vehicle evacuation can use (skip footways/paths/etc.).
DRIVABLE_HIGHWAYS = frozenset(
    {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
        "living_street",
        "road",
    }
)

# Path to the committed offline fixture (a few junctions, no network needed).
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "mini_roads.overpass.json"


# ---------------------------------------------------------------- Overpass JSON
def parse_overpass(
    data: dict[str, Any] | str,
    *,
    drivable_only: bool = True,
) -> RoadGraph:
    """Build a :class:`RoadGraph` from an Overpass JSON response (pure).

    ``data`` is the decoded JSON dict (or a raw JSON string). Each ``way`` becomes
    a chain of bidirectional road segments between its consecutive nodes; segment
    weights default to the haversine length of each hop. ``oneway=yes`` ways are
    added in a single direction. With ``drivable_only`` (default) non-vehicle
    highways are skipped.
    """
    if isinstance(data, str):
        data = json.loads(data)

    elements = data.get("elements", []) if isinstance(data, dict) else []
    coords: dict[int, LatLon] = {}
    ways: list[dict[str, Any]] = []
    for el in elements:
        et = el.get("type")
        if et == "node":
            coords[int(el["id"])] = LatLon(float(el["lat"]), float(el["lon"]))
        elif et == "way":
            ways.append(el)

    g = RoadGraph()
    for way in ways:
        tags = way.get("tags") or {}
        highway = tags.get("highway")
        if drivable_only and highway is not None and highway not in DRIVABLE_HIGHWAYS:
            continue
        node_ids = way.get("nodes") or []
        way_id = way.get("id", "?")
        name = tags.get("name") or tags.get("ref")
        oneway = str(tags.get("oneway", "")).lower() in ("yes", "true", "1")
        for i in range(len(node_ids) - 1):
            a = coords.get(int(node_ids[i]))
            b = coords.get(int(node_ids[i + 1]))
            if a is None or b is None:
                continue  # node referenced but not returned (clipped at bbox)
            g.add_edge(
                a,
                b,
                segment_id=f"way/{way_id}:{i}",
                name=name,
                bidirectional=not oneway,
            )
    return g


# ------------------------------------------------------------------- GeoJSON
def parse_geojson(data: dict[str, Any] | str) -> RoadGraph:
    """Build a :class:`RoadGraph` from a GeoJSON ``FeatureCollection`` of lines.

    Each ``LineString`` / ``MultiLineString`` feature is split into per-vertex
    road segments. GeoJSON coordinates are ``[lon, lat]``.
    """
    if isinstance(data, str):
        data = json.loads(data)

    features = data.get("features", []) if isinstance(data, dict) else []
    g = RoadGraph()
    for fi, feat in enumerate(features):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        props = feat.get("properties") or {}
        name = props.get("name") or props.get("ref")
        if gtype == "LineString":
            lines = [geom.get("coordinates") or []]
        elif gtype == "MultiLineString":
            lines = geom.get("coordinates") or []
        else:
            continue
        for li, line in enumerate(lines):
            for i in range(len(line) - 1):
                lon_a, lat_a = line[i][0], line[i][1]
                lon_b, lat_b = line[i + 1][0], line[i + 1][1]
                g.add_edge(
                    LatLon(float(lat_a), float(lon_a)),
                    LatLon(float(lat_b), float(lon_b)),
                    segment_id=f"feat/{fi}/{li}:{i}",
                    name=name,
                )
    return g


# ----------------------------------------------------------- offline fixture
def load_fixture(path: str | Path | None = None) -> RoadGraph:
    """Parse the committed offline road fixture into a :class:`RoadGraph`.

    No network, no injection — the canonical small graph used in tests, demos and
    degraded operation.
    """
    p = Path(path) if path is not None else FIXTURE_PATH
    return parse_overpass(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------- live fetch
def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    """Overpass QL to fetch drivable highways inside ``bbox`` (S, W, N, E)."""
    south, west, north, east = bbox
    classes = "|".join(sorted(DRIVABLE_HIGHWAYS))
    return (
        "[out:json][timeout:25];"
        f'way["highway"~"^({classes})$"]({south},{west},{north},{east});'
        "(._;>;);out body;"
    )


def fetch_roads(
    bbox: tuple[float, float, float, float],
    *,
    transport: Any = None,
    raw: dict[str, Any] | str | None = None,
    timeout: float = 25.0,
    url: str = OVERPASS_URL,
    drivable_only: bool = True,
) -> RoadGraph:
    """Fetch the road network for ``bbox`` (S, W, N, E) as a :class:`RoadGraph`.

    Production path: POSTs/GETs an Overpass query through the shared HTTP
    transport seam (:mod:`disastermind.tier3.ingestion.http`). Tests pass
    ``raw=`` (a recorded Overpass JSON payload) or ``transport=`` (an injected
    ``(url, timeout) -> (status, text)`` callable) so **no real network call**
    ever happens under test. ``httpx`` is preferred when installed and falls back
    to stdlib ``urllib`` — both lazy, neither imported at module load.
    """
    if raw is not None:
        return parse_overpass(raw, drivable_only=drivable_only)

    # Lazy import: the HTTP seam is only needed for a live fetch.
    from ..tier3.ingestion.http import http_get_json

    query = overpass_query(bbox)
    request_url = f"{url}?data={query}"
    data = http_get_json(request_url, timeout=timeout, transport=transport)
    return parse_overpass(data, drivable_only=drivable_only)
