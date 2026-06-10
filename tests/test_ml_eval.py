"""Tests for :mod:`disastermind.ml.eval` — the model evaluation / backtest harness
that addresses the Step 3 "validated accuracy" gap.

Stdlib-only: every assertion below holds with NO optional dependency and NO
network. The backtest exercises the real fit/predict path; where a *real* trained
backend (xgboost/sklearn/numpy) materially changes behaviour, the relevant cases
either tolerate both outcomes (heuristic fallback vs real backend) or guard with
:func:`pytest.importorskip`. Determinism is asserted directly: same seed => same
result.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys

import pytest

from disastermind.core.contracts import Module
from disastermind.ml.eval import (
    CalibrationBin,
    Metrics,
    accuracy_at,
    backtest,
    backtest_module,
    brier_score,
    calibration_bins,
    evaluate,
    expected_calibration_error,
    model_card,
    roc_auc,
    to_markdown,
    train_test_split,
)
from disastermind.ml.eval.backtest import MODULES
from disastermind.ml.models import HeuristicRiskModel

ALL_MODULES = (Module.CYCLONE_FLOOD, Module.EARTHQUAKE, Module.FIRE_COLLAPSE)


# --------------------------------------------------------------------------- AUC
def test_auc_perfectly_separable_is_one() -> None:
    # Every positive scores strictly above every negative -> AUC == 1.0 exactly.
    y_true = [0, 0, 0, 1, 1, 1]
    y_prob = [0.01, 0.10, 0.20, 0.70, 0.85, 0.99]
    assert roc_auc(y_true, y_prob) == 1.0


def test_auc_perfectly_inverted_is_zero() -> None:
    # Positives score strictly below negatives -> AUC == 0.0 exactly.
    y_true = [0, 0, 0, 1, 1, 1]
    y_prob = [0.99, 0.85, 0.70, 0.20, 0.10, 0.01]
    assert roc_auc(y_true, y_prob) == 0.0


def test_auc_random_is_near_half() -> None:
    rng = random.Random(1234)
    y_true = [rng.randint(0, 1) for _ in range(4000)]
    y_prob = [rng.random() for _ in range(4000)]
    auc = roc_auc(y_true, y_prob)
    assert abs(auc - 0.5) < 0.05  # no signal -> ~0.5


def test_auc_ties_credited_half() -> None:
    # One positive and one negative share the SAME score -> that pair counts 0.5.
    # 1 pos, 1 neg, identical score => U = 0.5 => AUC = 0.5.
    assert roc_auc([0, 1], [0.5, 0.5]) == 0.5


def test_auc_single_class_returns_half() -> None:
    # AUC undefined with one class; we return the no-skill 0.5 convention.
    assert roc_auc([1, 1, 1], [0.2, 0.5, 0.9]) == 0.5
    assert roc_auc([0, 0, 0], [0.2, 0.5, 0.9]) == 0.5


# --------------------------------------------------------------------------- Brier
def test_brier_matches_hand_computed() -> None:
    # y=[1,0,1], p=[0.8,0.3,0.6]:
    #   ((0.8-1)^2 + (0.3-0)^2 + (0.6-1)^2) / 3
    # = (0.04 + 0.09 + 0.16) / 3 = 0.29 / 3.
    expected = (0.2 ** 2 + 0.3 ** 2 + 0.4 ** 2) / 3
    assert brier_score([1, 0, 1], [0.8, 0.3, 0.6]) == pytest.approx(expected)


def test_brier_perfect_is_zero_and_worst_is_one() -> None:
    assert brier_score([0, 1], [0.0, 1.0]) == pytest.approx(0.0)
    assert brier_score([0, 1], [1.0, 0.0]) == pytest.approx(1.0)


# --------------------------------------------------------------------------- accuracy
def test_accuracy_at_threshold() -> None:
    y_true = [0, 0, 1, 1]
    y_prob = [0.2, 0.6, 0.4, 0.9]  # at 0.5: pred [0,1,0,1] vs [0,0,1,1] -> 2/4
    assert accuracy_at(y_true, y_prob, threshold=0.5) == pytest.approx(0.5)
    # Lower threshold flips the 0.4 and 0.6 predictions.
    assert accuracy_at(y_true, y_prob, threshold=0.35) == pytest.approx(0.75)


# --------------------------------------------------------------------------- calibration
def test_calibration_bins_count_sums_to_n() -> None:
    rng = random.Random(7)
    n = 250
    y_true = [rng.randint(0, 1) for _ in range(n)]
    y_prob = [rng.random() for _ in range(n)]
    for n_bins in (1, 5, 10, 20):
        bins = calibration_bins(y_true, y_prob, n_bins=n_bins)
        assert len(bins) == n_bins
        assert sum(b.count for b in bins) == n  # every row lands in exactly one bin


def test_calibration_bins_tile_unit_interval() -> None:
    bins = calibration_bins([0, 1], [0.0, 1.0], n_bins=10)
    assert bins[0].lower == pytest.approx(0.0)
    assert bins[-1].upper == pytest.approx(1.0)
    # p == 0.0 lands in first bin, p == 1.0 in the last bin.
    assert bins[0].count == 1
    assert bins[-1].count == 1


def test_perfect_calibration_has_zero_ece() -> None:
    # Two bins, each with predicted == observed -> ECE == 0.
    # bin [0.0,0.1): four rows pred 0.0 obs 0.0; bin [0.9,1.0]: four rows pred 1.0 obs 1.0
    y_true = [0, 0, 0, 0, 1, 1, 1, 1]
    y_prob = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
    bins = calibration_bins(y_true, y_prob, n_bins=10)
    assert expected_calibration_error(bins) == pytest.approx(0.0)


def test_calibration_bin_is_dataclass() -> None:
    b = calibration_bins([1], [0.95], n_bins=10)[-1]
    assert isinstance(b, CalibrationBin)
    assert b.to_dict()["count"] == 1


# --------------------------------------------------------------------------- evaluate
def test_evaluate_bundles_all_metrics() -> None:
    y_true = [0, 0, 0, 1, 1, 1]
    y_prob = [0.01, 0.10, 0.20, 0.70, 0.85, 0.99]
    m = evaluate(y_true, y_prob)
    assert isinstance(m, Metrics)
    assert m.n == 6
    assert m.positives == 3
    assert m.prevalence == pytest.approx(0.5)
    assert m.auc == 1.0
    assert 0.0 <= m.brier <= 1.0
    assert m.accuracy == 1.0
    # to_dict must be JSON round-trippable.
    blob = json.dumps(m.to_dict())
    assert json.loads(blob)["auc"] == 1.0
    assert len(json.loads(blob)["calibration"]) == 10


def test_evaluate_accepts_continuous_labels() -> None:
    # Continuous risk labels are binarised at 0.5; AUC stays well-defined.
    y_true = [0.05, 0.2, 0.49, 0.51, 0.8, 0.95]
    y_prob = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
    m = evaluate(y_true, y_prob)
    assert m.positives == 3
    assert m.auc == 1.0


def test_evaluate_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        evaluate([0, 1], [0.5])


# --------------------------------------------------------------------------- split
def test_train_test_split_is_disjoint_and_deterministic() -> None:
    X = [[float(i)] for i in range(100)]
    y = [float(i % 2) for i in range(100)]
    a = train_test_split(X, y, test_fraction=0.25, seed=0)
    b = train_test_split(X, y, test_fraction=0.25, seed=0)
    assert a == b  # deterministic in seed
    X_tr, y_tr, X_te, y_te = a
    assert len(X_tr) + len(X_te) == 100
    assert len(X_te) == 25
    # No leakage: train and test rows are disjoint.
    tr = {row[0] for row in X_tr}
    te = {row[0] for row in X_te}
    assert tr.isdisjoint(te)
    # A different seed yields a different split.
    assert train_test_split(X, y, test_fraction=0.25, seed=1) != a


def test_train_test_split_bad_fraction_raises() -> None:
    with pytest.raises(ValueError):
        train_test_split([[1.0]], [1.0], test_fraction=0.0)
    with pytest.raises(ValueError):
        train_test_split([[1.0]], [1.0], test_fraction=1.0)


# --------------------------------------------------------------------------- backtest
@pytest.mark.parametrize("module", ALL_MODULES)
def test_backtest_module_runs_for_each(module: Module) -> None:
    entry = backtest_module(module, n=200, seed=0)
    assert entry["module"] == module.value
    assert entry["fitted"] is True
    assert entry["n_train"] + entry["n_test"] == 200
    met = entry["metrics"]
    assert 0.0 <= met["auc"] <= 1.0
    assert 0.0 <= met["brier"] <= 1.0
    assert 0.0 <= met["accuracy"] <= 1.0
    assert sum(b["count"] for b in met["calibration"]) == met["n"]
    # The synthetic signal is monotone and learnable -> the model (real backend or
    # the matching heuristic fallback) must beat chance comfortably.
    assert met["auc"] > 0.7


def test_backtest_all_modules_in_memory() -> None:
    result = backtest(seed=0, n=200)
    assert result["format"] == "disastermind.ml.eval/1"
    assert [e["module"] for e in result["modules"]] == [m.value for m in MODULES]
    assert {e["module"] for e in result["modules"]} == {"A", "B", "C"}
    # No out_dir => nothing written, no out_dir key.
    assert "out_dir" not in result


def test_backtest_is_deterministic() -> None:
    a = backtest(seed=3, n=160)
    b = backtest(seed=3, n=160)
    assert a == b


def test_backtest_writes_artifacts(tmp_path) -> None:
    out = str(tmp_path / "eval")
    result = backtest(out, seed=0, n=200)
    assert result["out_dir"] == os.path.abspath(out)
    bt = os.path.join(out, "backtest.json")
    assert os.path.exists(bt)
    with open(bt, encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["seed"] == 0
    for mv in ("A", "B", "C"):
        card = os.path.join(out, f"card_{mv}.md")
        assert os.path.exists(card)
        with open(card, encoding="utf-8") as fh:
            text = fh.read()
        assert len(text) > 50
        assert "synthetic" in text.lower()  # honest provenance present


# --------------------------------------------------------------------------- cards
@pytest.mark.parametrize("module", ALL_MODULES)
def test_model_card_renders_non_empty(module: Module) -> None:
    entry = backtest_module(module, n=160, seed=0)
    card = entry["card"]
    assert card["module"] == module.value
    assert card["features"]  # non-empty feature list
    assert card["intended_use"]
    assert card["limitations"]
    md = to_markdown(card)
    assert md.strip()  # non-empty
    assert f"Module {module.value}" in md
    assert "## Limitations" in md
    assert "## Held-out metrics" in md
    # The headline honesty: the backtest harness is synthetic and says so;
    # real-data evidence lives in disastermind.ml.validation.
    assert any("synthetic" in lim.lower() for lim in card["limitations"])
    assert any("validation" in lim.lower() for lim in card["limitations"])


def test_model_card_heuristic_fallback_limitation() -> None:
    # A pure heuristic model (no active backend) must surface the fallback caveat,
    # regardless of whether optional ML libraries are installed in this env.
    module = Module.EARTHQUAKE
    model = HeuristicRiskModel(module)
    model.fit([[6.0, 50.0, 1.0]], [0.7])
    assert model._backend_obj is None  # heuristic has no real backend object
    metrics = evaluate([0, 1, 1], [0.2, 0.6, 0.8])
    card = model_card(module, model, metrics, n_train=1)
    assert card["backend_active"] is False
    joined = " ".join(card["limitations"]).lower()
    assert "heuristic" in joined
    md = to_markdown(card)
    assert "heuristic fallback" in md.lower()


def test_model_card_to_markdown_is_pure_function() -> None:
    metrics = evaluate([0, 1], [0.1, 0.9])
    card = model_card(Module.FIRE_COLLAPSE, HeuristicRiskModel(Module.FIRE_COLLAPSE), metrics)
    md1 = to_markdown(card)
    md2 = to_markdown(card)
    assert md1 == md2  # rendering does not mutate the card


# --------------------------------------------------------------------------- CLI
def test_eval_module_cli_runs(tmp_path) -> None:
    out = str(tmp_path / "cli")
    proc = subprocess.run(
        [sys.executable, "-m", "disastermind.ml.eval", "--out", out, "--n", "120", "--seed", "0"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["format"] == "disastermind.ml.eval/1"
    assert len(payload["modules"]) == 3
    assert os.path.exists(os.path.join(out, "backtest.json"))


# --------------------------------------------------------------------------- real backend
def test_backtest_with_real_backend_high_auc() -> None:
    # When the real stack is present, the learned model should score strongly on
    # the held-out monotone synthetic data. Skips cleanly on a bare stdlib install.
    pytest.importorskip("numpy")
    pytest.importorskip("xgboost")
    entry = backtest_module(Module.EARTHQUAKE, n=400, seed=0)
    assert entry["backend_active"] is True
    assert entry["metrics"]["auc"] > 0.85
