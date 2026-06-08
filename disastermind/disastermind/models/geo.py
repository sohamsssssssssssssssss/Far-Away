"""Geospatial primitives shared across prediction, routing and resourcing.

Kept stdlib-only (haversine via ``math``) so importing the domain model never
requires shapely/geopandas. Heavy spatial work (PostGIS queries) lives behind
the agents that need it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float

    def distance_m(self, other: "LatLon") -> float:
        """Great-circle distance in metres (haversine)."""
        p1, p2 = math.radians(self.lat), math.radians(other.lat)
        dphi = math.radians(other.lat - self.lat)
        dlmb = math.radians(other.lon - self.lon)
        a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
        return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float

    def contains(self, p: LatLon) -> bool:
        return self.south <= p.lat <= self.north and self.west <= p.lon <= self.east

    def center(self) -> LatLon:
        return LatLon((self.south + self.north) / 2, (self.west + self.east) / 2)


@dataclass(frozen=True)
class GridCell:
    """A fixed-size grid cell (default 100 m, PRD Step 3 Module A)."""

    row: int
    col: int
    size_m: int = 100

    @property
    def id(self) -> str:
        return f"{self.size_m}m:{self.row}:{self.col}"

    @staticmethod
    def from_latlon(p: LatLon, size_m: int = 100, origin: LatLon | None = None) -> "GridCell":
        origin = origin or LatLon(0.0, 0.0)
        # local equirectangular approximation — adequate at city/district scale
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(origin.lat))
        dy = (p.lat - origin.lat) * m_per_deg_lat
        dx = (p.lon - origin.lon) * m_per_deg_lon
        return GridCell(int(dy // size_m), int(dx // size_m), size_m)


def haversine(a: LatLon, b: LatLon) -> float:
    return a.distance_m(b)
