"""Parsing / coercion helpers for the evacuation routing agent.

Best-effort conversion of loosely-typed message payloads (dicts, tuples) into the
strongly-typed domain dataclasses the routing solver works with. All coercions
return ``None`` on malformed input rather than raising, so degraded payloads
silently drop the offending element instead of crashing the agent.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ...models.domain import (
    CascadeFailure,
    EvacRoute,
    Shelter,
)
from ...models.geo import LatLon


def _evacroute_to_dict(route: EvacRoute) -> dict[str, Any]:
    """asdict-based serialisation (LatLon dataclasses become nested dicts)."""
    return asdict(route)


def _as_latlon(value: Any) -> LatLon | None:
    if value is None:
        return None
    if isinstance(value, LatLon):
        return value
    if isinstance(value, dict):
        try:
            return LatLon(float(value["lat"]), float(value["lon"]))
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return LatLon(float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def _as_shelter(value: Any) -> Shelter | None:
    if value is None:
        return None
    if isinstance(value, Shelter):
        return value
    if isinstance(value, dict):
        loc = _as_latlon(value.get("location"))
        if loc is None:
            return None
        try:
            return Shelter(
                shelter_id=str(value.get("shelter_id", "shelter")),
                location=loc,
                capacity=int(value.get("capacity", 0) or 0),
                occupancy=int(value.get("occupancy", 0) or 0),
            )
        except (TypeError, ValueError):
            return None
    return None


def _as_cascade_failure(value: Any) -> CascadeFailure | None:
    if value is None:
        return None
    if isinstance(value, CascadeFailure):
        return value
    if isinstance(value, dict):
        try:
            return CascadeFailure(
                segment_id=str(value.get("segment_id", "seg")),
                fails_at_minute=int(value.get("fails_at_minute", 0) or 0),
                reason=str(value.get("reason", "inundation")),
                viable_until_minute=int(value.get("viable_until_minute", 0) or 0),
            )
        except (TypeError, ValueError):
            return None
    return None
