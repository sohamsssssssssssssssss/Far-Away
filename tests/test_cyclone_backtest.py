"""National cyclone backtest across all real IBTrACS landfalling storms — offline.

Asserts the data is the real 92-storm set, region classification is honest
(non-India landfalls not forced onto Indian states), activation logic is sound,
and 'unknown' (no pre-cutoff wind) is never counted as activated.
"""
from __future__ import annotations

from disastermind.hindcast.cyclone_backtest import (
    CYCLONE_ALERT_KT,
    backtest_storm,
    classify_region,
    load_storms,
    run_national_backtest,
)


def test_fixture_is_the_real_92_storm_set():
    storms = load_storms()
    assert len(storms) == 92
    assert all("track" in s and "sid" in s for s in storms)


def test_region_classification_is_honest():
    # Odisha coast (Fani landfall ~20.2, 85.9) -> Odisha
    assert classify_region(20.2, 85.9) == "Odisha"
    # West Bengal / Sundarbans (Amphan ~22.1, 88.4)
    assert classify_region(22.1, 88.4) == "West Bengal / Sundarbans"
    # a Bangladesh landfall is NOT forced onto an Indian state
    assert classify_region(22.2, 91.8) == "Bangladesh"
    # open ocean -> Other, never a fabricated state
    assert classify_region(5.0, 95.0) == "Other / open-coast"


def test_activation_requires_alert_threshold_and_handles_unknown():
    # synthetic storm: strong wind well before landfall -> activates
    strong = {"sid": "X", "name": "T", "season": 2020, "max_wind_kt": 90,
              "track": [
                  {"time": "2020-05-01 00:00:00", "lat": 15.0, "lon": 87.0, "wind_kt": 60.0, "dist2land_km": 400.0},
                  {"time": "2020-05-04 00:00:00", "lat": 20.2, "lon": 85.9, "wind_kt": 90.0, "dist2land_km": 0.0},
              ]}
    r = backtest_storm(strong, lead_hours=72)
    assert r.activated is True and r.cutoff_wind_kt >= CYCLONE_ALERT_KT
    assert r.region == "Odisha"

    # no wind record before cutoff -> unknown, NOT activated
    blank = {"sid": "Y", "name": "U", "season": 1995, "max_wind_kt": None,
             "track": [
                 {"time": "1995-05-01 00:00:00", "lat": 15.0, "lon": 87.0, "wind_kt": None, "dist2land_km": 400.0},
                 {"time": "1995-05-04 00:00:00", "lat": 20.2, "lon": 85.9, "wind_kt": None, "dist2land_km": 0.0},
             ]}
    assert backtest_storm(blank, lead_hours=72).activated is None


def test_national_backtest_aggregates_and_never_inflates():
    bt = run_national_backtest()
    assert bt.total_storms == 92
    assert sum(r.storms for r in bt.regions) == 92  # every storm classified once
    # activated + unknown never exceeds the total; rate is over KNOWN verdicts only
    assert bt.activated + bt.unknown <= bt.total_storms
    assert 0.0 <= bt.activation_rate <= 1.0
    assert bt.india_landfalls > 0
    # determinism
    assert run_national_backtest().to_dict() == bt.to_dict()
