"""Road-network graph + shortest-path routing (PRD Step 5).

Evacuation routes must follow the *road network*, not straight haversine lines.
This module provides a small, pure-stdlib weighted graph whose

  * **nodes** are road junctions (:class:`~disastermind.models.geo.LatLon`), and
  * **edges** are road segments weighted by their length in metres
    (via :func:`~disastermind.models.geo.haversine`).

:class:`RoadGraph` supports :meth:`add_edge` / :meth:`neighbors`, Dijkstra and
A\\* :meth:`shortest_path` (a stdlib :mod:`heapq` priority queue — A\\* uses the
haversine straight-line distance as an *admissible* heuristic, so it returns the
same optimal distance as Dijkstra), and :meth:`route_latlons` to materialise a
path back into coordinates an evacuation router can drive.

Edges may be **closed** (consuming ``CascadeFailure``-style segment closures, see
:func:`~disastermind.roadnet.closures.close_segments`); closed edges are skipped
during search so routing detours around inundated / collapsed / burning roads.

Pure stdlib (``math`` + ``heapq``). No third-party imports, no network.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from ..models.geo import LatLon, haversine

# A node key — we key the graph on rounded (lat, lon) so equal junctions from
# different fixture rows collapse to one node. 7 dp ~= 1 cm, finer than any road.
NodeKey = tuple[float, float]

_PRECISION = 7


def node_key(p: LatLon) -> NodeKey:
    """Stable hashable key for a junction at ``p`` (rounded to ~1 cm)."""
    return (round(p.lat, _PRECISION), round(p.lon, _PRECISION))


@dataclass(frozen=True)
class Edge:
    """A directed road segment ``u -> v`` of ``length_m`` metres.

    ``segment_id`` lets external closures (``CascadeFailure.segment_id``) name an
    edge; ``closed`` marks it temporarily impassable without dropping it so it can
    be reopened later. ``name`` is the optional OSM ``name``/``ref`` tag.
    """

    u: NodeKey
    v: NodeKey
    length_m: float
    segment_id: str
    name: str | None = None
    closed: bool = False


@dataclass
class RoadGraph:
    """Weighted directed road graph keyed on rounded junction coordinates.

    Roads are bidirectional by default (``add_edge`` adds both directions). Each
    direction is its own :class:`Edge` sharing the same ``segment_id`` so closing
    a segment closes travel both ways.
    """

    # node key -> LatLon (canonical coordinate for that junction)
    nodes: dict[NodeKey, LatLon] = field(default_factory=dict)
    # node key -> list[Edge] leaving that node
    _adj: dict[NodeKey, list[Edge]] = field(default_factory=dict)
    # segment_id -> list of Edge objects sharing it (both directions)
    _by_segment: dict[str, list[Edge]] = field(default_factory=dict)

    # ----------------------------------------------------------------- building
    def add_node(self, p: LatLon) -> NodeKey:
        """Register ``p`` (idempotent) and return its node key."""
        k = node_key(p)
        self.nodes.setdefault(k, p)
        self._adj.setdefault(k, [])
        return k

    def add_edge(
        self,
        a: LatLon,
        b: LatLon,
        *,
        segment_id: str | None = None,
        length_m: float | None = None,
        name: str | None = None,
        bidirectional: bool = True,
    ) -> str:
        """Add a road segment between junctions ``a`` and ``b``.

        ``length_m`` defaults to the haversine distance (the road-length proxy
        for a straight OSM way segment). Returns the ``segment_id`` used.
        """
        ka, kb = self.add_node(a), self.add_node(b)
        if length_m is None:
            length_m = haversine(a, b)
        seg = segment_id or f"seg:{ka[0]},{ka[1]}->{kb[0]},{kb[1]}"

        fwd = Edge(ka, kb, float(length_m), seg, name)
        self._adj[ka].append(fwd)
        self._by_segment.setdefault(seg, []).append(fwd)
        if bidirectional:
            rev = Edge(kb, ka, float(length_m), seg, name)
            self._adj[kb].append(rev)
            self._by_segment[seg].append(rev)
        return seg

    # ----------------------------------------------------------------- querying
    def neighbors(self, k: NodeKey) -> Iterator[Edge]:
        """Yield the *open* edges leaving node ``k`` (closed edges are skipped)."""
        for e in self._adj.get(k, ()):  # noqa: SIM118
            if not e.closed:
                yield e

    def all_edges(self) -> Iterator[Edge]:
        """Yield every edge (open and closed), each direction once."""
        for edges in self._adj.values():
            yield from edges

    def segment_ids(self) -> set[str]:
        return set(self._by_segment)

    def __len__(self) -> int:  # node count
        return len(self.nodes)

    # ------------------------------------------------------------------ closures
    def set_closed(self, segment_id: str, closed: bool = True) -> bool:
        """Mark every edge of ``segment_id`` closed/open. Returns True if found.

        Edges are frozen dataclasses, so we replace them in place with a closed
        copy (preserving identity by ``segment_id``) and rebuild adjacency refs.
        """
        edges = self._by_segment.get(segment_id)
        if not edges:
            return False
        new_edges = [
            Edge(e.u, e.v, e.length_m, e.segment_id, e.name, closed) for e in edges
        ]
        self._by_segment[segment_id] = new_edges
        replaced = {id(old): new for old, new in zip(edges, new_edges)}
        for k, adj in self._adj.items():
            self._adj[k] = [replaced.get(id(e), e) for e in adj]
        return True

    def closed_segments(self) -> set[str]:
        return {
            seg
            for seg, edges in self._by_segment.items()
            if edges and all(e.closed for e in edges)
        }

    # ----------------------------------------------------------- shortest path
    def shortest_path(
        self,
        a: LatLon,
        b: LatLon,
        *,
        heuristic: bool = True,
    ) -> tuple[list[LatLon], float]:
        """Least-distance road path from ``a`` to ``b``.

        Returns ``(latlon_path, total_metres)``. With ``heuristic=True`` this is
        A\\* using the haversine straight-line distance to the goal as an
        admissible (never-overestimating) heuristic; with ``heuristic=False`` it
        is plain Dijkstra. Both return the same optimal distance. Raises
        :class:`NoRouteError` when no open path exists.
        """
        start, goal = node_key(a), node_key(b)
        if start not in self.nodes:
            raise NoRouteError(f"start junction {a} not in graph")
        if goal not in self.nodes:
            raise NoRouteError(f"goal junction {b} not in graph")
        if start == goal:
            return [self.nodes[start]], 0.0

        goal_p = self.nodes[goal]

        def h(k: NodeKey) -> float:
            if not heuristic:
                return 0.0
            # admissible: straight-line <= any road path
            return haversine(self.nodes[k], goal_p)

        # priority queue of (f = g + h, g, node)
        pq: list[tuple[float, float, NodeKey]] = [(h(start), 0.0, start)]
        best_g: dict[NodeKey, float] = {start: 0.0}
        came_from: dict[NodeKey, NodeKey] = {}

        while pq:
            _f, g, u = heapq.heappop(pq)
            if u == goal:
                return self._reconstruct(came_from, start, goal), g
            if g > best_g.get(u, float("inf")):
                continue  # stale queue entry
            for e in self.neighbors(u):
                ng = g + e.length_m
                if ng < best_g.get(e.v, float("inf")):
                    best_g[e.v] = ng
                    came_from[e.v] = u
                    heapq.heappush(pq, (ng + h(e.v), ng, e.v))

        raise NoRouteError(f"no open road path from {a} to {b}")

    def route_latlons(self, a: LatLon, b: LatLon, **kw) -> list[LatLon]:
        """Convenience: just the :class:`LatLon` waypoints of the shortest path."""
        return self.shortest_path(a, b, **kw)[0]

    def _reconstruct(
        self, came_from: dict[NodeKey, NodeKey], start: NodeKey, goal: NodeKey
    ) -> list[LatLon]:
        path_keys = [goal]
        cur = goal
        while cur != start:
            cur = came_from[cur]
            path_keys.append(cur)
        path_keys.reverse()
        return [self.nodes[k] for k in path_keys]


class NoRouteError(RuntimeError):
    """Raised when no open road path connects two junctions."""


def road_distance(graph: RoadGraph, a: LatLon, b: LatLon, **kw) -> float:
    """Road-network distance in metres an evac router can swap in for haversine.

    Drop-in for :func:`~disastermind.models.geo.haversine` but follows roads and
    respects closures. Raises :class:`NoRouteError` when unreachable so the caller
    can fall back to the straight-line estimate explicitly.
    """
    return graph.shortest_path(a, b, **kw)[1]


def build_graph_from_segments(segments: Iterable[dict]) -> RoadGraph:
    """Build a :class:`RoadGraph` from simple segment dicts (test/fixture helper).

    Each dict needs ``a``/``b`` as ``(lat, lon)`` (or ``{"lat","lon"}``) pairs and
    optionally ``segment_id``/``name``/``length_m``.
    """

    def _ll(v) -> LatLon:
        if isinstance(v, LatLon):
            return v
        if isinstance(v, dict):
            return LatLon(float(v["lat"]), float(v["lon"]))
        return LatLon(float(v[0]), float(v[1]))

    g = RoadGraph()
    for s in segments:
        g.add_edge(
            _ll(s["a"]),
            _ll(s["b"]),
            segment_id=s.get("segment_id"),
            length_m=s.get("length_m"),
            name=s.get("name"),
        )
    return g
