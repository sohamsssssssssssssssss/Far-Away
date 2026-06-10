"""Tests for :mod:`disastermind.ml.training` — the producer of the artefacts the
tier-2 prediction agents' ML seam loads (PRD Step 10).

Stdlib-only: every assertion below holds with NO optional dependency and NO
network. Assertions that require a *real* trained backend (xgboost/sklearn/numpy)
are guarded with :func:`pytest.importorskip`, so they skip (not fail) when those
libraries are absent. Determinism is asserted directly: same seed => same bytes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from disastermind.core.contracts import Module
from disastermind.ml import FEATURE_NAMES, RiskModel
from disastermind.ml.training import (
    MODULES,
    artifact_path,
    extreme_rows,
    label_for,
    load_trained,
    make_dataset,
    train_all,
    train_module,
)

ALL_MODULES = (Module.CYCLONE_FLOOD, Module.EARTHQUAKE, Module.FIRE_COLLAPSE)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """train_module/train_all cache fitted wrappers globally (real-data fits
    with a live sklearn backend genuinely change predictions); keep the suite
    hermetic so later pipeline tests see the stock heuristics."""
    from disastermind.ml.registry import reset_registry

    reset_registry()
    yield
    reset_registry()


# --------------------------------------------------------------------------- synthetic
@pytest.mark.parametrize("module", ALL_MODULES)
def test_synthetic_shape_matches_schema(module: Module) -> None:
    n = 64
    X, y = make_dataset(module, n=n, seed=0)
    assert len(X) == n
    assert len(y) == n
    width = len(FEATURE_NAMES[module])
    assert all(len(row) == width for row in X)
    assert all(isinstance(v, float) for row in X for v in row)
    assert all(0.0 <= v <= 1.0 for v in y)


@pytest.mark.parametrize("module", ALL_MODULES)
def test_synthetic_is_deterministic(module: Module) -> None:
    a = make_dataset(module, n=32, seed=7)
    b = make_dataset(module, n=32, seed=7)
    assert a == b  # same seed => byte-identical X and y
    c = make_dataset(module, n=32, seed=8)
    assert c != a  # different seed => different draw


@pytest.mark.parametrize("module", ALL_MODULES)
def test_label_monotonic_signal(module: Module) -> None:
    low, high = extreme_rows(module)
    # The noise-free underlying signal must rank max-hazard above min-hazard.
    assert label_for(module, high) > label_for(module, low)


def test_modules_cover_a_b_c() -> None:
    assert set(MODULES) == set(ALL_MODULES)


# --------------------------------------------------------------------------- train_all
def test_train_all_writes_loadable_artifacts(tmp_path) -> None:
    out = str(tmp_path / "models")
    manifest = train_all(out, n=64, seed=0)

    assert manifest["format"].startswith("disastermind.ml.training/")
    assert manifest["seed"] == 0
    assert len(manifest["models"]) == len(ALL_MODULES)

    for module in ALL_MODULES:
        path = artifact_path(out, module)
        assert os.path.exists(path), f"missing artefact for {module}"
        # Loads back via the registry's RiskModel.load contract.
        model = load_trained(out, module)
        assert isinstance(model, RiskModel)
        assert model.module is module
        assert tuple(model.feature_names) == FEATURE_NAMES[module]
        assert model.fitted is True

    # A manifest.json record is written alongside the artefacts.
    assert os.path.exists(os.path.join(os.path.abspath(out), "manifest.json"))


@pytest.mark.parametrize("module", ALL_MODULES)
def test_loaded_model_predicts_in_range_and_monotone(tmp_path, module: Module) -> None:
    out = str(tmp_path / "models")
    train_all(out, n=128, seed=1)
    model = load_trained(out, module)

    low, high = extreme_rows(module)
    preds = model.predict([low, high])
    assert len(preds) == 2
    assert all(0.0 <= p <= 1.0 for p in preds)
    # High-signal row scores at least as high as the low-signal row.
    assert preds[1] >= preds[0]


def test_train_all_is_reproducible(tmp_path) -> None:
    out_a = str(tmp_path / "a")
    out_b = str(tmp_path / "b")
    train_all(out_a, n=48, seed=3)
    train_all(out_b, n=48, seed=3)
    for module in ALL_MODULES:
        # Compare predictions on the extreme rows: identical seed => identical model.
        ma = load_trained(out_a, module)
        mb = load_trained(out_b, module)
        low, high = extreme_rows(module)
        assert ma.predict([low, high]) == mb.predict([low, high])


def test_train_module_entry_fields(tmp_path) -> None:
    out = str(tmp_path / "models")
    os.makedirs(out, exist_ok=True)
    entry = train_module(out, Module.EARTHQUAKE, n=40, seed=2)
    assert entry["module"] == Module.EARTHQUAKE.value
    assert entry["fitted"] is True
    assert entry["n_train"] == 40
    assert entry["seed"] == 2
    assert entry["feature_names"] == list(FEATURE_NAMES[Module.EARTHQUAKE])
    assert os.path.exists(entry["path"])


# --------------------------------------------------------------------------- CLI
def test_cli_trains_and_prints_manifest(tmp_path) -> None:
    out = str(tmp_path / "cli_models")
    proc = subprocess.run(
        [sys.executable, "-m", "disastermind.ml.training", "--out", out, "--n", "32"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert len(printed["models"]) == len(ALL_MODULES)
    for module in ALL_MODULES:
        assert os.path.exists(artifact_path(out, module))


# --------------------------------------------------------------------------- backends
def test_trained_xgboost_backend_active(tmp_path) -> None:
    pytest.importorskip("xgboost")
    pytest.importorskip("numpy")
    out = str(tmp_path / "xgb")
    train_all(out, n=128, seed=0)
    # Modules A/B prefer the XGBoost wrapper; with the lib present it really trains.
    model = load_trained(out, Module.EARTHQUAKE)
    assert model.backend == "xgboost"
    assert model._backend_obj is not None  # native booster restored from artefact
    low, high = extreme_rows(Module.EARTHQUAKE)
    preds = model.predict([low, high])
    assert all(0.0 <= p <= 1.0 for p in preds)


def test_trained_sklearn_backend_active(tmp_path) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("numpy")
    out = str(tmp_path / "sk")
    train_all(out, n=200, seed=0)
    # Module C prefers the scikit-learn logistic baseline.
    model = load_trained(out, Module.FIRE_COLLAPSE)
    assert model.backend == "sklearn"
    assert model._backend_obj is not None
    low, high = extreme_rows(Module.FIRE_COLLAPSE)
    preds = model.predict([low, high])
    assert all(0.0 <= p <= 1.0 for p in preds)
    assert preds[1] >= preds[0]
