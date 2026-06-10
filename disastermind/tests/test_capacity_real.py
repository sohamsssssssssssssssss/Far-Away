"""Shelter-capacity-vs-population validation (Puri / Fani zone) — offline.

Against the committed real fixture (Census-derived population + real OSM shelter
footprint areas). Asserts the capacity arithmetic, that a real shortfall exists
and is correctly flagged, and that the data is genuinely real.
"""
from __future__ import annotations

from disastermind.hindcast.capacity import (
    PACKED_M2,
    SPHERE_M2,
    load_capacity,
    validate_capacity,
)


def test_fixture_is_real():
    fx = load_capacity()
    pop_tags = fx["population_tags"]
    assert pop_tags and int(pop_tags[0][1]) > 100000  # real Puri Census figure
    assert any(s.get("name") == "Jagannath Temple" for s in fx["shelters"])
    assert all(s.get("area_m2", 0) > 0 for s in fx["shelters"])  # real footprint areas


def test_capacity_arithmetic_matches_densities():
    v = validate_capacity()
    # capacity = total floor area / density (transparent, reproducible)
    assert v.capacity_sphere == int(v.total_floor_m2 / SPHERE_M2)
    assert v.capacity_packed == int(v.total_floor_m2 / PACKED_M2)
    assert v.capacity_packed > v.capacity_sphere  # packed holds more


def test_real_shortfall_exists_and_is_flagged():
    v = validate_capacity()
    # OSM-tagged shelters cannot hold Puri's population — a real, large shortfall
    assert v.coverage_sphere_pct < 50.0
    assert v.shortfall_sphere > 0 and v.shortfall_packed > 0
    # and the system flags it (correct behaviour, not silent failure)
    assert v.system_flags_gap is True


def test_coverage_and_shortfall_are_consistent():
    v = validate_capacity()
    assert v.shortfall_sphere == v.population - v.capacity_sphere
    # coverage % tracks capacity/population
    assert abs(v.coverage_sphere_pct - 100.0 * v.capacity_sphere / v.population) < 0.1
