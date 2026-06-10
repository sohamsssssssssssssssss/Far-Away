"""Blocked cross-validation: leave-one-region-out + rolling-origin."""
from __future__ import annotations

import random

from disastermind.ml.eval.crossval import leave_one_region_out, rolling_origin, summarise


def _fit(Xtr, ytr):
    """Tiny deterministic 'model': score = first feature (already informative)."""
    return lambda Xq: [min(1.0, max(0.0, row[0])) for row in Xq]


def _blocked_data(n_per_block: int = 80, seed: int = 3):
    rng = random.Random(seed)
    X, y, regions, years = [], [], [], []
    for region in ("north", "south", "east"):
        for year in (2018, 2019, 2020, 2021):
            for _ in range(n_per_block):
                signal = rng.random()
                X.append([signal, rng.random()])
                y.append(1 if signal > 0.6 else 0)
                regions.append(region)
                years.append(year)
    return X, y, regions, years


def test_loro_yields_one_fold_per_region_and_real_skill():
    X, y, regions, years = _blocked_data()
    folds = leave_one_region_out(X, y, regions, _fit, min_test=10)
    assert sorted(f.held_out for f in folds) == ["east", "north", "south"]
    for f in folds:
        # the held-out block was never trained on
        assert f.n_train + f.n_test == len(y)
        assert f.auc > 0.9  # the signal transfers across blocks


def test_loro_skips_tiny_or_single_class_regions():
    X = [[0.1], [0.9], [0.2], [0.8], [0.5]]
    y = [0, 1, 0, 1, 1]
    regions = ["a", "a", "a", "a", "tiny"]
    folds = leave_one_region_out(X, y, regions, _fit, min_test=2)
    assert all(f.held_out != "tiny" for f in folds)


def test_rolling_origin_respects_causality():
    X, y, regions, years = _blocked_data()
    folds = rolling_origin(X, y, years, _fit, min_train_years=2, min_test=10)
    # first two years only ever train; each later year becomes one test fold
    assert [f.held_out for f in folds] == ["2020", "2021"]
    for f in folds:
        held = int(f.held_out)
        n_earlier = sum(1 for yr in years if yr < held)
        assert f.n_train == n_earlier  # trained strictly on the past


def test_summarise_reports_worst_not_just_mean():
    X, y, regions, years = _blocked_data()
    s = summarise(leave_one_region_out(X, y, regions, _fit, min_test=10))
    assert s["folds"] == 3
    assert s["auc_worst"] <= s["auc_mean"] <= s["auc_best"]
    assert len(s["per_fold"]) == 3


def test_summarise_empty_is_explicit():
    s = summarise([])
    assert s == {"folds": 0, "auc_worst": None, "auc_mean": None, "auc_best": None}
