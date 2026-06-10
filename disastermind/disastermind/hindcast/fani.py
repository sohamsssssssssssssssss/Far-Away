"""Load the committed real Cyclone Fani (2019) best-track + documented outcome.

Source: NOAA IBTrACS v04r01 (North Indian Ocean). The fixture is the real
71-point best-track for FANI plus an authoritative documented-outcome block
(IMD RSMC report, EM-DAT, Odisha Special Relief Commissioner). No network.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "fani_2019.json")


@dataclass
class TrackPoint:
    time: str  # ISO "YYYY-MM-DD HH:MM:SS" (UTC, IBTrACS)
    lat: float
    lon: float
    wind_kt: float | None
    pres_mb: float | None
    dist2land_km: float | None


@dataclass
class FaniCase:
    track: list[TrackPoint]
    outcome: dict
    source: str
    source_url: str
    storm: str = "FANI"
    season: int = 2019

    def landfall_point(self) -> TrackPoint:
        """First best-track point at the coast (dist2land == 0) — real landfall."""
        for p in self.track:
            if p.dist2land_km is not None and p.dist2land_km <= 0.0:
                return p
        # fallback: closest approach
        return min(self.track, key=lambda p: (p.dist2land_km if p.dist2land_km is not None else 1e9))

    def points_before(self, cutoff_iso: str) -> list[TrackPoint]:
        """Strictly leak-free: only best-track points at or before the cutoff."""
        return [p for p in self.track if p.time <= cutoff_iso]


def load_case(path: str) -> FaniCase:
    """Load any committed storm fixture (Fani, Amphan, ...) into a case."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    track = [
        TrackPoint(
            time=p["time"],
            lat=float(p["lat"]),
            lon=float(p["lon"]),
            wind_kt=p.get("wind_kt"),
            pres_mb=p.get("pres_mb"),
            dist2land_km=p.get("dist2land_km"),
        )
        for p in raw["track"]
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    return FaniCase(
        track=track,
        outcome=raw["documented_outcome"],
        source=raw.get("source", "NOAA IBTrACS"),
        source_url=raw.get("source_url", ""),
        storm=raw.get("storm", "FANI"),
        season=int(raw.get("season", 2019)),
    )


def load_fani(path: str = FIXTURE) -> FaniCase:
    return load_case(path)


AMPHAN_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "amphan_2020.json")


def load_amphan(path: str = AMPHAN_FIXTURE) -> FaniCase:
    return load_case(path)
