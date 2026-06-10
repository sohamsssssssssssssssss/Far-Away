"""Drift detection (PSI/KS), retraining trigger and decay-curve plumbing."""
from __future__ import annotations

import random

import pytest

from disastermind.ml.eval.crossval import Fold
from disastermind.ml.eval.drift import (
    PSI_DRIFTED,
    feature_drift,
    ks_statistic,
    psi,
    retrain_decision,
)


def _ref(n: int = 2000, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


# ------------------------------------------------------------------------ scores
def test_psi_near_zero_for_same_distribution():
    assert psi(_ref(seed=1), _ref(seed=2)) < 0.05


def test_psi_large_for_shifted_distribution():
    shifted = [v + 2.0 for v in _ref(seed=3)]
    assert psi(_ref(seed=1), shifted) > PSI_DRIFTED


def test_psi_rejects_empty():
    with pytest.raises(ValueError):
        psi([], [1.0])


def test_ks_detects_shift_and_passes_identical():
    d_same, p_same = ks_statistic(_ref(seed=1), _ref(seed=2))
    d_shift, p_shift = ks_statistic(_ref(seed=1), [v + 1.0 for v in _ref(seed=3)])
    assert d_shift > d_same
    assert p_shift < 0.01 < p_same


def test_feature_drift_labels_columns():
    Xr = [[v, v] for v in _ref(500, seed=1)]
    Xl = [[v, v + 3.0] for v in _ref(500, seed=2)]  # only column b drifts
    drifts = {d.feature: d for d in feature_drift(("a", "b"), Xr, Xl)}
    assert drifts["a"].status == "stable"
    assert drifts["b"].status == "drifted"


# --------------------------------------------------------------------- decision
def _fold(year: int, auc: float) -> Fold:
    return Fold(held_out=str(year), n_train=100, n_test=50, positives=10, auc=auc, brier=0.1)


def test_retrain_fires_on_feature_drift():
    Xr = [[v] for v in _ref(300, seed=1)]
    Xl = [[v + 3.0] for v in _ref(300, seed=2)]
    decision = retrain_decision(feature_drift(("f",), Xr, Xl), [])
    assert decision.retrain and decision.drifted_features == ("f",)


def test_retrain_fires_on_skill_decay():
    folds = [_fold(2019, 0.90), _fold(2020, 0.91), _fold(2021, 0.78)]
    decision = retrain_decision([], folds, max_auc_drop=0.05)
    assert decision.retrain
    assert any("decay" in r for r in decision.reasons)
    assert decision.auc_recent == 0.78


def test_no_signal_means_hold():
    Xr = [[v] for v in _ref(300, seed=1)]
    Xl = [[v] for v in _ref(300, seed=2)]
    folds = [_fold(2019, 0.90), _fold(2020, 0.91), _fold(2021, 0.90)]
    decision = retrain_decision(feature_drift(("f",), Xr, Xl), folds)
    assert not decision.retrain and decision.reasons == ()


def test_too_few_folds_abstains_from_decay_claim():
    decision = retrain_decision([], [_fold(2021, 0.5)])
    assert not decision.retrain
    assert decision.auc_recent is None  # no evidence either way, says so
