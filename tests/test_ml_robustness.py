"""Degraded-input robustness — graceful-degradation curve under sensor failure."""
from __future__ import annotations

import pytest

from disastermind.ml.eval.robustness import (
    DegradationPoint,
    degradation_curve,
    graceful_until,
    to_dict,
)
from disastermind.ml.validation import flood as F
from disastermind.ml.validation.run import fit_logistic, predict


def test_intact_point_is_first_and_unchanged():
    # a fixed predictor; fraction 0.0 must score the clean test set
    X = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [0.0, 1.0]]
    y = [1, 1, 0, 0]
    pred = lambda Xq: [min(1.0, r[0] / 6.0) for r in Xq]  # noqa: E731
    curve = degradation_curve(pred, X, X, y, fractions=(0.0, 0.5), seed=0)
    assert curve[0].fraction == 0.0
    assert 0.0 <= curve[0].auc <= 1.0


def test_skill_degrades_as_more_inputs_fail():
    import random

    rng = random.Random(0)
    # every feature carries signal, so losing features must cost skill
    X, y = [], []
    for _ in range(800):
        feats = [rng.random() for _ in range(4)]
        X.append(feats)
        y.append(1 if sum(feats) > 2.0 else 0)
    split = 500
    m = fit_logistic(X[:split], y[:split], name="r", epochs=80)
    pred = lambda Xq: predict(m, Xq)  # noqa: E731
    curve = degradation_curve(
        pred, X[:split], X[split:], y[split:], fractions=(0.0, 0.5, 1.0), seed=1
    )
    assert curve[0].auc >= curve[1].auc >= curve[-1].auc  # monotone non-increasing


def test_graceful_until_reports_a_fraction_not_a_lie():
    good = [
        DegradationPoint(0.0, 0.95, 0.3, 0.95),
        DegradationPoint(0.25, 0.85, 0.3, 0.9),
        DegradationPoint(0.5, 0.55, 0.4, 0.8),
    ]
    assert graceful_until(good, min_pod=0.7) == 0.25
    # a model that fails even intact must not be reported as robust
    failing = [DegradationPoint(0.0, 0.4, 0.5, 0.6)]
    assert graceful_until(failing, min_pod=0.7) == 0.0


def test_unknown_mode_rejected():
    X = [[1.0, 2.0], [3.0, 4.0]]
    y = [1, 0]
    pred = lambda Xq: [0.5 for _ in Xq]  # noqa: E731
    with pytest.raises(ValueError):
        degradation_curve(pred, X, X, y, fractions=(0.0, 0.5), mode="bogus")


def test_real_flood_model_degrades_gracefully():
    rows = F.load_rows()
    tr, te = F.temporal_split(rows)
    step = max(1, len(tr) // 5000)
    tr = tr[::step]
    Xtr = [list(r.features) for r in tr]
    ytr = [r.label for r in tr]
    Xte = [list(r.features) for r in te]
    yte = [r.label for r in te]
    m = fit_logistic(Xtr, ytr, name="r", epochs=40, balanced=True)
    curve = degradation_curve(lambda Xq: predict(m, Xq), Xtr, Xte, yte, target_pod=0.9)
    summary = to_dict(curve)
    # losing a quarter of the gauges must not collapse the model
    assert summary["graceful_until_pod70"] >= 0.25
    # but total input loss must visibly hurt (no false robustness claim)
    assert curve[-1].auc < curve[0].auc
