"""Paired-bootstrap significance tests (model vs baseline with p-values)."""
from __future__ import annotations

import random

import pytest

from disastermind.ml.eval.metrics import roc_auc
from disastermind.ml.eval.significance import (
    bootstrap_ci,
    compare_auc,
    compare_brier,
    paired_bootstrap,
)


def _data(n: int = 400, seed: int = 1):
    rng = random.Random(seed)
    y = [1 if rng.random() < 0.3 else 0 for _ in range(n)]
    good = [0.7 * lab + 0.3 * rng.random() for lab in y]  # informative
    bad = [rng.random() for _ in y]  # noise
    return y, good, bad


def test_clearly_better_model_is_significant():
    y, good, bad = _data()
    cmp = compare_auc(y, good, bad, n_boot=200, seed=0)
    assert cmp.model_score > cmp.baseline_score
    assert cmp.p_value < 0.05 and cmp.significant
    assert cmp.ci_low > 0  # the whole CI sits above zero


def test_noise_vs_itself_is_not_significant():
    y, _, bad = _data()
    cmp = compare_auc(y, bad, list(bad), n_boot=100, seed=0)
    assert not cmp.significant
    assert cmp.delta_mean == pytest.approx(0.0, abs=1e-12)


def test_brier_comparison_flips_the_sign_convention():
    y, good, bad = _data()
    cmp = compare_brier(y, good, bad, n_boot=100, seed=0)
    assert cmp.metric == "brier" and not cmp.higher_is_better
    assert cmp.model_score < cmp.baseline_score  # lower Brier is better
    assert cmp.significant


def test_deterministic_for_a_seed():
    y, good, bad = _data()
    a = compare_auc(y, good, bad, n_boot=50, seed=7)
    b = compare_auc(y, good, bad, n_boot=50, seed=7)
    assert a == b
    c = compare_auc(y, good, bad, n_boot=50, seed=8)
    assert c.delta_mean != a.delta_mean  # different resamples


def test_paired_bootstrap_validates_inputs():
    with pytest.raises(ValueError):
        paired_bootstrap([1], [0.5], [0.5, 0.5], metric=roc_auc, metric_name="auc")
    with pytest.raises(ValueError):
        paired_bootstrap([], [], [], metric=roc_auc, metric_name="auc")


def test_bootstrap_ci_brackets_the_observed_value():
    y, good, _ = _data()
    obs, lo, hi = bootstrap_ci(y, good, metric=roc_auc, n_boot=200, seed=0)
    assert lo <= obs <= hi
    assert 0.5 < obs <= 1.0
