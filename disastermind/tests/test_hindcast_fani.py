"""Fani 2019 hindcast tests — offline, against the committed real IBTrACS track.

Asserts the methodology is leak-free and the load-bearing findings hold: the
storm's real landfall is on the Odisha coast, the replay uses only pre-cutoff
data, the system activates with multi-day lead time, the coordination pipeline
produces a plan, and a naive landfall extrapolation sharpens as landfall nears.
"""
from __future__ import annotations

from disastermind.hindcast import load_fani, run_hindcast
from disastermind.hindcast.replay import extrapolate_landfall


def test_fixture_is_real_fani_track():
    case = load_fani()
    assert len(case.track) > 50  # the real 71-point best-track
    assert "IBTrACS" in case.source
    lf = case.landfall_point()
    # real Fani landfall: near Puri, Odisha (~20.2 N, 85.9 E), 2019-05-03
    assert lf.time.startswith("2019-05-03")
    assert 19.0 < lf.lat < 21.5 and 84.5 < lf.lon < 87.0
    # documented outcome present and authoritative
    assert case.outcome["deaths"] == 64
    assert case.outcome["damage_usd_billion"] > 0


def test_replay_is_leak_free():
    """Each cutoff must use only points at/before it (no peeking past the cutoff)."""
    case = load_fani()
    r = run_hindcast(case, lead_hours=24.0)
    before = case.points_before(r.cutoff_time)
    assert before and all(p.time <= r.cutoff_time for p in before)
    # the landfall point itself is AFTER the 24 h cutoff -> never seen by the forecast
    assert case.landfall_point().time > r.cutoff_time


def test_system_activates_with_multiday_lead():
    """The load-bearing finding: activation days before landfall (the evac window)."""
    case = load_fani()
    for lead in (72.0, 48.0, 24.0):
        r = run_hindcast(case, lead_hours=lead)
        assert r.activated, f"failed to activate at {lead} h lead"
        assert r.produced_plan, f"no coordination plan at {lead} h lead"
        assert r.dispatches > 0


def test_landfall_extrapolation_sharpens_near_landfall():
    """A naive extrapolation is poor far out but tightens close in (honest)."""
    case = load_fani()
    far = run_hindcast(case, lead_hours=72.0).track_error_km
    near = run_hindcast(case, lead_hours=24.0).track_error_km
    assert near < far  # closer to landfall -> smaller error
    assert near < 100.0  # within ~100 km a day out, from track alone


def test_extrapolate_handles_sparse_input():
    case = load_fani()
    pts = case.points_before("2019-04-27 00:00:00")
    lat, lon = extrapolate_landfall(pts, "2019-05-03 06:00:00")
    assert -10 < lat < 40 and 60 < lon < 100  # plausible basin coordinates


# ------------------------------------------------------------------ Amphan (2020)
def test_amphan_is_real_and_activates_in_time():
    """Second real event: the activation/plan pattern must hold, not just for Fani."""
    from disastermind.hindcast.fani import load_amphan

    case = load_amphan()
    assert case.storm == "AMPHAN" and case.season == 2020
    lf = case.landfall_point()
    # real Amphan landfall: West Bengal/Sundarbans (~22.1 N, 88.4 E), 2020-05-20
    assert lf.time.startswith("2020-05-20")
    assert 21.0 < lf.lat < 23.5 and 87.5 < lf.lon < 89.5
    assert case.outcome["deaths"] == 128
    for lead in (72.0, 48.0, 24.0):
        r = run_hindcast(case, lead_hours=lead)
        assert r.activated and r.produced_plan and r.dispatches > 0


def test_amphan_landfall_extrapolation_improves_with_lead():
    from disastermind.hindcast.fani import load_amphan

    case = load_amphan()
    far = run_hindcast(case, lead_hours=72.0).track_error_km
    near = run_hindcast(case, lead_hours=12.0).track_error_km
    assert near < far  # sharpens approaching landfall
