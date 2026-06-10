"""Apply ``CascadeFailure``-style road closures to a :class:`RoadGraph`.

The cascade tier publishes :class:`~disastermind.models.domain.CascadeFailure`
objects naming road/bridge ``segment_id``\\s that are projected to fail
(inundation / high MMI / fire path). An evacuation router should treat those
segments as impassable and route *around* them.

These helpers are pure and stdlib-only: they flip the ``closed`` flag on the
matching edges (see :meth:`RoadGraph.set_closed`) and never mutate the cascade
objects themselves. They accept either real ``CascadeFailure`` instances, plain
dicts (the on-the-wire payload shape), or bare ``segment_id`` strings, so callers
do not need to import the domain model.
"""
from __future__ import annotations

from typing import Iterable

from .graph import RoadGraph


def _segment_id(item) -> str | None:
    """Coerce a CascadeFailure / dict / string into its ``segment_id``."""
    if item is None:
        return None
    if isinstance(item, str):
        return item
    sid = getattr(item, "segment_id", None)
    if sid is not None:
        return sid
    if isinstance(item, dict):
        return item.get("segment_id")
    return None


def close_segments(graph: RoadGraph, closures: Iterable) -> list[str]:
    """Close every named segment on ``graph`` and return the IDs actually closed.

    ``closures`` may mix ``CascadeFailure`` objects, dicts and segment-id strings.
    Unknown segment ids are silently ignored (they may belong to a different
    incident / road network), so this is safe to call with a global cascade feed.
    """
    closed: list[str] = []
    for item in closures or ():
        sid = _segment_id(item)
        if sid is not None and graph.set_closed(sid, True):
            closed.append(sid)
    return closed


def open_segments(graph: RoadGraph, segments: Iterable) -> list[str]:
    """Re-open previously closed segments (e.g. a cascade window expiring)."""
    reopened: list[str] = []
    for item in segments or ():
        sid = _segment_id(item)
        if sid is not None and graph.set_closed(sid, False):
            reopened.append(sid)
    return reopened


def reroute_around(graph: RoadGraph, a, b, closures: Iterable, **kw):
    """Close ``closures`` then return the detoured shortest path.

    Convenience wrapper: applies the closures and computes
    :meth:`RoadGraph.shortest_path` on the resulting (reduced) network so the
    returned route already avoids every failed segment. The closures persist on
    the graph; call :func:`open_segments` to restore them.
    """
    close_segments(graph, closures)
    return graph.shortest_path(a, b, **kw)
