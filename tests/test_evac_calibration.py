"""Tests for the evacuation clearance calibration harness.

These use *synthetic* records generated from a known ground-truth parameter set —
the point is to prove the fit recovers parameters and reduces error, NOT to claim
any real-world validation (that needs real agency records; see
docs/EVAC_CALIBRATION.md).
"""
from __future__ import annotations

from disastermind.evacuation.calibration import (
    EvacRecord,
    calibrate,
    load_records_csv,
)
from disastermind.evacuation.clearance import estimate_clearance


def _synthetic_truth(mob, part):
    """Build records whose observed clearance follows a known (mob, part)."""
    zones = [("A", 50_000, 3000.0), ("B", 120_000, 5000.0),
             ("C", 30_000, 2000.0), ("D", 200_000, 8000.0)]
    recs = []
    for zone, pop, egress in zones:
        obs = estimate_clearance(pop, egress, participation=part,
                                 mobilization_hours=mob).clearance_hours
        recs.append(EvacRecord(zone, pop, egress, obs))
    return recs


def test_calibration_recovers_known_parameters():
    # Ground truth differs from the model defaults (4.0 h, 0.9).
    recs = _synthetic_truth(mob=6.5, part=0.75)
    result = calibrate(recs)
    assert abs(result.fitted_mobilization_hours - 6.5) < 0.2
    assert abs(result.fitted_participation - 0.75) < 0.05


def test_calibration_reduces_error():
    recs = _synthetic_truth(mob=7.0, part=0.6)
    result = calibrate(recs)
    # Fitting must not be worse than the defaults, and here should be far better.
    assert result.mae_after <= result.mae_before
    assert result.improvement_pct() > 0.0


def test_per_record_residuals_present():
    recs = _synthetic_truth(mob=5.0, part=0.8)
    result = calibrate(recs)
    assert result.n == len(recs)
    assert len(result.per_record) == len(recs)
    assert all("residual_h" in r for r in result.per_record)


def test_empty_records_raise():
    try:
        calibrate([])
    except ValueError:
        return
    raise AssertionError("expected ValueError on empty records")


def test_load_records_csv(tmp_path):
    p = tmp_path / "recs.csv"
    p.write_text(
        "zone,population,egress_capacity_pph,observed_clearance_hours,observed_participation\n"
        "Puri,50000,3000,21.5,0.85\n"
        "Konark,18000,1500,14.0,\n"
    )
    recs = load_records_csv(str(p))
    assert len(recs) == 2
    assert recs[0].zone == "Puri"
    assert recs[0].observed_participation == 0.85
    assert recs[1].observed_participation is None
