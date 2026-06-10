"""Shared helpers for the persistence layer (PRD Step 9 storage).

Stdlib-only coercion/serialisation utilities used by every repository so that
JSON-dict payloads round-trip cleanly against the typed domain model
(:mod:`disastermind.models.domain`). Kept tiny and dependency-free; the heavy
backends (psycopg / elasticsearch / minio) are imported lazily *inside* the
repositories, never here.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from ..models.geo import LatLon


def coerce_latlon(obj: Any) -> LatLon:
    """Coerce a dict / pair / :class:`LatLon` into a :class:`LatLon`.

    Payloads cross the bus as JSON dicts (PRD Step 9), so spatial repos must
    accept ``{"lat": .., "lon": ..}`` as well as native objects.
    """
    if isinstance(obj, LatLon):
        return obj
    if isinstance(obj, dict):
        return LatLon(float(obj.get("lat", 0.0)), float(obj.get("lon", 0.0)))
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        return LatLon(float(obj[0]), float(obj[1]))
    raise TypeError(f"cannot coerce {obj!r} to LatLon")


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / enums to JSON-safe primitives."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    # enum members carry a primitive ``.value`` (str/int) in this codebase
    value = getattr(obj, "value", None)
    if value is not None and isinstance(value, (str, int, float, bool)):
        return value
    return obj
