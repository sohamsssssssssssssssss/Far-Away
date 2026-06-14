"""Lightweight geometry helpers for the evacuation routing agent.

These provide the stdlib-only geometric primitives the nearest-neighbour
fallback solver uses to localise hazards (encoded in segment ids) and to test
whether a straight evacuation leg passes too close to a closed segment.
"""
from __future__ import annotations

from ...models.geo import LatLon


def _segment_point(segment_id: str) -> LatLon | None:
    """Best-effort decode of a hazard location from a segment id.

    Convention: a segment id may embed coordinates as ``...lat,lon`` or
    ``lat:lon``; otherwise we cannot localise it and return None (the segment is
    still avoided by id elsewhere, just not geometrically).
    """
    if not segment_id:
        return None
    for sep in (",", ":"):
        if sep in segment_id:
            tail = segment_id.replace(":", ",").split(",")
            nums: list[float] = []
            for tok in tail:
                try:
                    nums.append(float(tok))
                except ValueError:
                    continue
            if len(nums) >= 2:
                return LatLon(nums[-2], nums[-1])
    return None


def _point_near_segment(p: LatLon, a: LatLon, b: LatLon) -> float:
    """Approx distance (m) from point ``p`` to leg ``a->b`` (planar projection)."""
    import math

    # Equirectangular metres relative to a.
    m_lat = 111_320.0
    m_lon = 111_320.0 * math.cos(math.radians(a.lat))
    ax, ay = 0.0, 0.0
    bx = (b.lon - a.lon) * m_lon
    by = (b.lat - a.lat) * m_lat
    px = (p.lon - a.lon) * m_lon
    py = (p.lat - a.lat) * m_lat
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)
