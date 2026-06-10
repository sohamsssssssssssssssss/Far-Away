"""Flood-risk-vs-real-terrain validation (Fani zone) — offline.

Against the committed real Copernicus-DEM grid. Asserts the data is real, that the
CURRENT distance-based risk map is poorly grounded in terrain (the honest negative
finding), and that the elevation-aware fix is substantially better grounded (the
demonstrated improvement).
"""
from __future__ import annotations

from disastermind.hindcast.terrain import (
    LOWLAND_M,
    distance_risk,
    elevation_aware_risk,
    load_dem,
    validate_terrain,
)


def test_dem_grid_is_real():
    cells = load_dem()
    assert len(cells) > 100  # the real fetched grid
    elevs = [c["elev_m"] for c in cells]
    assert min(elevs) <= 1.0 and max(elevs) > 50.0  # coast at sea level, inland hills
    assert any(c["elev_m"] < LOWLAND_M for c in cells)  # real low-lying cells exist


def test_current_model_is_poorly_grounded():
    """Honest negative finding: distance-based risk misses real flood-prone terrain."""
    v = validate_terrain()
    assert v.current.top_risk_lowland_frac < 0.3  # few top-risk cells are real lowland
    assert v.current.corr_with_lowland < 0.2  # weak/none alignment with low elevation


def test_elevation_aware_fix_is_better_grounded():
    """The demonstrated improvement using the real DEM."""
    v = validate_terrain()
    assert v.elevation_aware.top_risk_lowland_frac > v.current.top_risk_lowland_frac
    assert v.elevation_aware.corr_with_lowland > v.current.corr_with_lowland
    # the fix concentrates risk on genuinely low-lying cells
    assert v.elevation_aware.top_risk_lowland_frac > 0.7


def test_risk_functions_behave_physically():
    cells = load_dem()
    # at equal surge proximity, the elevation-aware risk prefers the lower cell
    low = {"lat": 19.8, "lon": 85.83, "elev_m": 0.0}
    high = {"lat": 19.8, "lon": 85.83, "elev_m": 40.0}
    assert elevation_aware_risk(low) > elevation_aware_risk(high)
    # distance risk ignores elevation (the whole point) — equal here
    assert distance_risk(low) == distance_risk(high)
