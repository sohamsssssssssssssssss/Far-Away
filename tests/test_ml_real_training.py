"""Production training runs on REAL fixtures — no synthetic in the pipeline."""
from __future__ import annotations

import json
import os

import pytest

from disastermind.core.contracts import Module
from disastermind.ml.features import FEATURE_NAMES
from disastermind.ml.registry import reset_registry
from disastermind.ml.training import MODULES, train_all, train_module
from disastermind.ml.training.real import make_real_dataset


@pytest.fixture(autouse=True)
def _isolated_registry():
    """train_module caches fitted wrappers globally; keep tests hermetic."""
    reset_registry()
    yield
    reset_registry()


@pytest.mark.parametrize("module", MODULES)
def test_real_dataset_matches_runtime_schema(module):
    X, y = make_real_dataset(module, n=300)
    assert len(X) == len(y) <= 300
    assert all(len(row) == len(FEATURE_NAMES[module]) for row in X)
    assert all(v in (0.0, 1.0) for v in y)  # real observed outcomes, not noise
    assert any(v == 1.0 for v in y) and any(v == 0.0 for v in y)  # both classes survive capping


@pytest.mark.parametrize("module", MODULES)
def test_real_dataset_is_deterministic(module):
    a = make_real_dataset(module, n=120)
    b = make_real_dataset(module, n=120)
    assert a == b  # no RNG, no clock — byte-for-byte reproducible


def test_real_labels_follow_the_hazard_signal():
    """Higher hazard drivers must carry a higher empirical event rate."""
    X, y = make_real_dataset(Module.EARTHQUAKE)  # (magnitude, distance_km, construction)
    big = [lab for row, lab in zip(X, y) if row[0] >= 6.5]
    small = [lab for row, lab in zip(X, y) if row[0] < 5.0]
    assert sum(big) / len(big) > sum(small) / len(small)


def test_default_training_source_is_real(tmp_path):
    entry = train_module(str(tmp_path), Module.EARTHQUAKE, n=200)
    assert entry["data_source"] == "real"
    assert entry["fitted"]


def test_train_all_manifest_records_real_provenance(tmp_path):
    manifest = train_all(str(tmp_path), n=150)
    assert manifest["data_source"] == "real"
    assert all(e["data_source"] == "real" for e in manifest["models"])
    on_disk = json.load(open(os.path.join(str(tmp_path), "manifest.json")))
    assert on_disk["data_source"] == "real"


def test_synthetic_is_optin_for_tests_only(tmp_path):
    entry = train_module(str(tmp_path), Module.FIRE_COLLAPSE, n=64, seed=1, source="synthetic")
    assert entry["data_source"] == "synthetic"
    with pytest.raises(ValueError):
        train_module(str(tmp_path), Module.FIRE_COLLAPSE, source="nonsense")
