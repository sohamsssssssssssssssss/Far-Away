"""Calibrated uncertainty: isotonic (PAV) recalibration + split-conformal sets."""
from __future__ import annotations

import random

from disastermind.ml.eval.conformal import (
    IsotonicCalibrator,
    calibration_report,
    coverage_report,
    fit_conformal,
    fit_isotonic,
)


def _underconfident(n: int = 2000, seed: int = 5):
    """Scores systematically too low: true rate ~ min(1, 2*score)."""
    rng = random.Random(seed)
    p = [rng.random() * 0.5 for _ in range(n)]
    y = [1 if rng.random() < min(1.0, 2.0 * s) else 0 for s in p]
    return y, p


# ----------------------------------------------------------------------- isotonic
def test_isotonic_output_is_monotone_and_in_range():
    y, p = _underconfident()
    iso = fit_isotonic(y, p)
    grid = [i / 100 for i in range(101)]
    out = iso.transform(grid)
    assert all(0.0 <= v <= 1.0 for v in out)
    assert all(a <= b for a, b in zip(out, out[1:]))  # non-decreasing


def test_isotonic_repairs_underconfidence_on_held_out_data():
    y_cal, p_cal = _underconfident(seed=5)
    y_te, p_te = _underconfident(seed=6)  # fresh draw, same miscalibration
    iso = fit_isotonic(y_cal, p_cal)
    rep = calibration_report(y_te, p_te, iso.transform(p_te))
    assert rep["ece_calibrated"] < rep["ece_raw"]  # measured, not assumed


def test_isotonic_round_trips_as_json():
    y, p = _underconfident(200)
    iso = fit_isotonic(y, p)
    clone = IsotonicCalibrator.from_dict(iso.to_dict())
    probe = [0.0, 0.1, 0.25, 0.49, 0.5]
    assert clone.transform(probe) == iso.transform(probe)


def test_isotonic_empty_is_identity():
    iso = fit_isotonic([], [])
    assert iso.transform([0.3, 0.9]) == [0.3, 0.9]


# ---------------------------------------------------------------------- conformal
def test_conformal_coverage_meets_target_on_exchangeable_data():
    y_cal, p_cal = _underconfident(seed=7)
    y_te, p_te = _underconfident(seed=8)
    clf = fit_conformal(y_cal, p_cal, alpha=0.1)
    rep = coverage_report(clf, y_te, p_te)
    # finite-sample guarantee: coverage >= 1 - alpha (small slack for noise)
    assert rep["coverage"] >= 0.88
    assert rep["singleton_rate"] + rep["abstain_rate"] + rep["empty_rate"] == 1.0


def test_confident_rows_get_singletons_uncertain_rows_get_sets():
    # calibration saw mostly-confident rows plus a band of genuinely
    # uncertain ones (mixed labels at p ~ 0.5)
    y_cal = [0] * 40 + [0, 1] * 10 + [1] * 40
    p_cal = [0.05] * 40 + [0.5] * 20 + [0.95] * 40
    clf = fit_conformal(y_cal, p_cal, alpha=0.1)
    assert clf.predict_set(0.97) == (1,)
    assert clf.predict_set(0.03) == (0,)
    assert set(clf.predict_set(0.5)) == {0, 1}  # genuinely unsure -> abstain set


def test_conformal_validates_inputs():
    import pytest

    with pytest.raises(ValueError):
        fit_conformal([1], [0.5, 0.5])
    with pytest.raises(ValueError):
        fit_conformal([1], [0.5], alpha=1.5)
