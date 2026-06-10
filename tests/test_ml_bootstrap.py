"""Tests for :mod:`disastermind.ml.bootstrap` — auto-loading trained artefacts.

These verify the WIRING: after training artefacts to a directory,
``ensure_models_loaded`` registers them so ``get_model`` returns the trained
model (engaging the prediction agents' real backend paths), while with no
directory it is a no-op and the system stays on heuristics.

Stdlib-only, no network: artefacts are produced by ``train_all`` into a tmp dir
and round-tripped from disk. The registry is reset between cases for isolation.
"""
from __future__ import annotations

import importlib
import os

import pytest

from disastermind.core.contracts import Module
from disastermind.ml.bootstrap import (
    MODELS_DIR_ENV,
    ensure_models_loaded,
    resolve_models_dir,
)
from disastermind.ml.registry import get_model, reset_registry
from disastermind.ml.training import artifact_path, train_all

_MODULES = (Module.CYCLONE_FLOOD, Module.EARTHQUAKE, Module.FIRE_COLLAPSE)

# A live backend object is only produced when the optional ML libraries are
# installed; gate the strict ``_backend_obj`` assertion on their presence so the
# test is correct on a bare stdlib install too (where models degrade to the
# heuristic and ``_backend_obj`` legitimately stays ``None``).
_HAVE_BACKENDS = all(
    importlib.util.find_spec(name) is not None
    for name in ("numpy", "xgboost", "sklearn")
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Isolate the process-wide model registry around every test."""
    reset_registry()
    yield
    reset_registry()


def _train(tmp_path) -> str:
    """Train artefacts to a tmp dir, then reset the registry.

    ``train_all`` fits models via ``get_model(..., fresh=True)`` and leaves those
    fitted instances in the process-wide cache. Resetting afterwards mirrors a
    real deployment (artefacts trained in one process, loaded fresh in another)
    and isolates what ``ensure_models_loaded`` does from training's side effects.
    """
    out = os.path.join(str(tmp_path), "models")
    train_all(out, n=64, seed=0)
    reset_registry()
    return out


# --------------------------------------------------------------------------- load
def test_ensure_loads_trained_artifacts(tmp_path):
    out = _train(tmp_path)

    loaded = ensure_models_loaded(models_dir=out)

    assert loaded == {m.value: True for m in _MODULES}
    for module in _MODULES:
        model = get_model(module)
        assert model.module is module
        assert model.fitted is True
        if _HAVE_BACKENDS:
            assert model._backend_obj is not None


def test_loaded_model_is_the_registered_instance(tmp_path):
    out = _train(tmp_path)
    ensure_models_loaded(models_dir=out)
    # get_model must keep returning the same (trained) cached instance.
    first = get_model(Module.EARTHQUAKE)
    second = get_model(Module.EARTHQUAKE)
    assert first is second
    assert first.fitted is True


# ------------------------------------------------------------------------- no-op
def test_no_dir_is_a_noop(monkeypatch):
    monkeypatch.delenv(MODELS_DIR_ENV, raising=False)

    loaded = ensure_models_loaded()

    assert loaded == {m.value: False for m in _MODULES}
    for module in _MODULES:
        model = get_model(module)
        assert model.fitted is False
        assert model._backend_obj is None


def test_missing_dir_is_a_noop(tmp_path):
    missing = os.path.join(str(tmp_path), "does-not-exist")

    loaded = ensure_models_loaded(models_dir=missing)

    assert loaded == {m.value: False for m in _MODULES}
    assert get_model(Module.CYCLONE_FLOOD).fitted is False


def test_empty_dir_is_a_noop(tmp_path):
    empty = os.path.join(str(tmp_path), "empty")
    os.makedirs(empty, exist_ok=True)

    loaded = ensure_models_loaded(models_dir=empty)

    assert loaded == {m.value: False for m in _MODULES}
    assert get_model(Module.FIRE_COLLAPSE).fitted is False


def test_partial_artifacts_load_only_present_modules(tmp_path):
    out = _train(tmp_path)
    # Remove module B's manifest so only A and C are loadable.
    os.remove(artifact_path(out, Module.EARTHQUAKE))

    loaded = ensure_models_loaded(models_dir=out)

    assert loaded[Module.CYCLONE_FLOOD.value] is True
    assert loaded[Module.FIRE_COLLAPSE.value] is True
    assert loaded[Module.EARTHQUAKE.value] is False
    assert get_model(Module.CYCLONE_FLOOD).fitted is True
    # B kept its fresh, untrained wrapper.
    assert get_model(Module.EARTHQUAKE).fitted is False


# ----------------------------------------------------------------- idempotency
def test_idempotent_repeated_calls(tmp_path):
    out = _train(tmp_path)

    first = ensure_models_loaded(models_dir=out)
    second = ensure_models_loaded(models_dir=out)

    assert first == second == {m.value: True for m in _MODULES}
    for module in _MODULES:
        assert get_model(module).fitted is True


# ----------------------------------------------------------- dir resolution
def test_env_var_resolves_models_dir(tmp_path, monkeypatch):
    out = _train(tmp_path)
    monkeypatch.setenv(MODELS_DIR_ENV, out)

    loaded = ensure_models_loaded()

    assert loaded == {m.value: True for m in _MODULES}


def test_explicit_arg_overrides_env(tmp_path, monkeypatch):
    out = _train(tmp_path)
    monkeypatch.setenv(MODELS_DIR_ENV, os.path.join(str(tmp_path), "nope"))

    loaded = ensure_models_loaded(models_dir=out)

    assert loaded == {m.value: True for m in _MODULES}


def test_settings_models_dir_attribute(tmp_path, monkeypatch):
    out = _train(tmp_path)
    monkeypatch.delenv(MODELS_DIR_ENV, raising=False)

    class _Settings:
        models_dir = out

    loaded = ensure_models_loaded(settings=_Settings())

    assert loaded == {m.value: True for m in _MODULES}


def test_resolve_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv(MODELS_DIR_ENV, raising=False)
    assert resolve_models_dir() is None
    # Blank values are treated as unset.
    assert resolve_models_dir(models_dir="   ") is None


def test_resolve_blank_env_is_unset(monkeypatch):
    monkeypatch.setenv(MODELS_DIR_ENV, "   ")
    assert resolve_models_dir() is None


def test_resolve_arg_is_absolute(tmp_path):
    out = _train(tmp_path)
    resolved = resolve_models_dir(models_dir=out)
    assert resolved == os.path.abspath(out)
