"""Score the flood-risk map against REAL terrain — and demonstrate the fix.

Elevation is the dominant physical determinant of where storm surge and rainfall
actually pool. This loads a real Copernicus-DEM elevation grid over the Cyclone
Fani coastal flood zone (Puri/Khordha) and asks whether the system's flood-risk
map is *physically grounded* in it.

The honest finding is a NEGATIVE one, surfaced rather than hidden: the current
prediction assigns risk by **distance from landfall**, not terrain, so its
highest-risk cells are mostly NOT the real low-lying flood-prone areas. This
module quantifies that gap AND demonstrates the concrete, reachable fix — an
elevation-aware risk that combines surge proximity with the real DEM puts risk
where the water really goes.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
from dataclasses import dataclass, field

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "fani_dem_grid.json")

#: Fani landfall (NOAA IBTrACS): Odisha coast near Puri.
LANDFALL = (20.2, 85.9)
#: Storm-surge/flood pooling depth scale (m) — water collects below a few metres.
FLOOD_ELEV_SCALE = 5.0
#: Surge-reach distance decay scale from landfall (km).
SURGE_DECAY_KM = 40.0
#: "Low-lying / flood-prone" elevation threshold (m).
LOWLAND_M = 5.0


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


def _corr(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else 0.0


def distance_risk(cell: dict) -> float:
    """The system's CURRENT flood risk: distance-decay from landfall (no terrain)."""
    d = _haversine_km((cell["lat"], cell["lon"]), LANDFALL)
    return math.exp(-d / SURGE_DECAY_KM)


def elevation_aware_risk(cell: dict) -> float:
    """The FIX: surge proximity AND real low elevation (water pools in lowlands)."""
    surge = distance_risk(cell)
    low = math.exp(-max(0.0, cell["elev_m"]) / FLOOD_ELEV_SCALE)
    return surge * low


@dataclass
class TerrainScore:
    model: str
    corr_with_lowland: float  # corr(risk, -elevation): higher = better grounded
    top_risk_lowland_frac: float  # fraction of top-20% risk cells that are <5 m


@dataclass
class TerrainValidation:
    cells: int
    elev_min: float
    elev_max: float
    lowland_cells: int
    current: TerrainScore
    elevation_aware: TerrainScore
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["current"] = self.current.__dict__
        d["elevation_aware"] = self.elevation_aware.__dict__
        return d


def load_dem(path: str = FIXTURE) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _score(cells: list[dict], risk_fn, name: str) -> TerrainScore:
    risks = [risk_fn(c) for c in cells]
    elevs = [c["elev_m"] for c in cells]
    ranked = sorted(zip(cells, risks, strict=False), key=lambda t: -t[1])
    top = ranked[: max(1, len(ranked) // 5)]
    low_frac = sum(1 for c, _ in top if c["elev_m"] < LOWLAND_M) / len(top)
    return TerrainScore(
        model=name,
        corr_with_lowland=round(_corr(risks, [-e for e in elevs]), 3),
        top_risk_lowland_frac=round(low_frac, 3),
    )


def validate_terrain(cells: list[dict] | None = None) -> TerrainValidation:
    cells = cells if cells is not None else load_dem()
    elevs = [c["elev_m"] for c in cells]
    lowland = sum(1 for e in elevs if e < LOWLAND_M)
    current = _score(cells, distance_risk, "distance-from-landfall (current)")
    fixed = _score(cells, elevation_aware_risk, "elevation-aware (proposed fix)")
    notes = [
        "Real Copernicus-DEM elevation grid over the Fani coastal flood zone "
        f"({len(cells)} cells, {min(elevs):.0f}-{max(elevs):.0f} m).",
        f"CURRENT model: only {current.top_risk_lowland_frac:.0%} of its top-20% "
        "highest-risk cells are real low-lying (<5 m) flood-prone terrain — its risk "
        "tracks distance from landfall, not where water actually pools.",
        f"ELEVATION-AWARE fix: combining surge proximity with the real DEM raises "
        f"that to {fixed.top_risk_lowland_frac:.0%} — risk lands on the real lowlands. "
        "The DEM is free and reachable (Copernicus via Open-Meteo), so this is a "
        "concrete, actionable model improvement, not a hypothetical.",
        "Honest scope: low elevation is a physical PROXY for flood-proneness; true "
        "ground truth is mapped inundation extent (Copernicus EMS EMSR353 for Fani), "
        "which needs GIS/portal access beyond this harness.",
    ]
    return TerrainValidation(
        cells=len(cells),
        elev_min=min(elevs),
        elev_max=max(elevs),
        lowland_cells=lowland,
        current=current,
        elevation_aware=fixed,
        notes=notes,
    )


def to_markdown(v: TerrainValidation) -> str:
    return "\n".join(
        [
            "# Flood-Risk Map vs REAL Terrain — Cyclone Fani coastal zone",
            "",
            "_Real Copernicus-DEM elevation grid (Open-Meteo) over the Puri/Khordha "
            "coast. Is the system's flood-risk map physically grounded in terrain?_",
            "",
            "## Real terrain",
            f"- **DEM grid:** {v.cells} cells, elevation {v.elev_min:.0f}-{v.elev_max:.0f} m "
            f"({v.lowland_cells} real low-lying <5 m flood-prone cells)",
            "",
            "## Is the risk map grounded? (higher = better)",
            "| Risk model | corr with low elevation | top-20% risk that is real lowland |",
            "|---|---|---|",
            f"| {v.current.model} | {v.current.corr_with_lowland} | "
            f"**{v.current.top_risk_lowland_frac:.0%}** |",
            f"| {v.elevation_aware.model} | {v.elevation_aware.corr_with_lowland} | "
            f"**{v.elevation_aware.top_risk_lowland_frac:.0%}** |",
            "",
            "## Finding (honest)",
            *[f"- {n}" for n in v.notes],
        ]
    )
