"""Training pipeline — fit + persist one risk model per module (PRD Step 10).

This is the producer side of the ML seam the tier-2 prediction agents consume:
the agents call :func:`disastermind.ml.get_model` and (when present) load a fitted
artefact; this module *creates* those artefacts.

``train_all(out_dir, seed=0)`` does, for each module A/B/C:

  1. build a deterministic REAL training table (:mod:`.real` — USGS quakes,
     GloFAS/ERA5 floods, FPA-FOD/ERA5 wildfires; ``source="synthetic"`` keeps
     the legacy generator available for tests, never for shipped artefacts),
  2. obtain the module's wrapper via :func:`disastermind.ml.get_model`,
  3. ``.fit(X, y)`` — a real backend (xgboost/sklearn/numpy) trains if installed,
     otherwise the wrapper stays on its deterministic stdlib heuristic (it never
     hard-fails),
  4. ``.save(path)`` the artefact,

and returns a JSON-serialisable manifest describing every artefact.
``load_trained(out_dir, module)`` restores one artefact via ``RiskModel.load``.

Everything here is stdlib-only on the import + happy path; optional ML libraries
are reached only lazily *inside* the wrappers. No network, no wall-clock: the same
``seed`` reproduces the same artefacts.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ...core.contracts import Module
from ..features import FEATURE_NAMES
from ..models import RiskModel
from ..registry import get_model
from .real import make_real_dataset
from .synthetic import make_dataset

#: Modules trained, in stable A/B/C order.
MODULES: tuple[Module, ...] = (
    Module.CYCLONE_FLOOD,
    Module.EARTHQUAKE,
    Module.FIRE_COLLAPSE,
)

#: Manifest format tag (parallels disastermind.ml.models' "disastermind.ml/1").
MANIFEST_FORMAT = "disastermind.ml.training/1"

#: Default number of synthetic training rows per module.
DEFAULT_N = 256


def artifact_path(out_dir: str, module: Module | str) -> str:
    """Deterministic artefact path for ``module`` under ``out_dir``.

    Named ``model_<value>.json`` (e.g. ``model_A.json``) so a module maps to a
    single stable filename that :func:`load_trained` can recompute. Accepts a
    :class:`Module` or its string value (``"A"``/``"B"``/``"C"``) for parity with
    :func:`disastermind.ml.get_model`.
    """
    module = module if isinstance(module, Module) else Module(module)
    return os.path.join(out_dir, f"model_{module.value}.json")


def train_module(
    out_dir: str,
    module: Module,
    *,
    n: int | None = DEFAULT_N,
    seed: int = 0,
    source: str = "real",
) -> dict[str, Any]:
    """Fit + save one module's model; return its manifest entry.

    ``source="real"`` (the default, and the only mode used for shipped
    artefacts) trains on the committed real fixtures via
    :func:`disastermind.ml.training.real.make_real_dataset`, with ``n`` as a
    stratified row cap; ``source="synthetic"`` keeps the legacy generator for
    unit tests that need a controllable signal. Uses a *fresh* wrapper
    (``get_model(..., fresh=True)``) so repeated training runs in one process
    never inherit a previously-fitted cached instance.
    """
    if source == "real":
        X, y = make_real_dataset(module, n=n)
    elif source == "synthetic":
        X, y = make_dataset(module, n=n if n is not None else DEFAULT_N, seed=seed)
    else:
        raise ValueError(f"unknown training source {source!r}")
    model: RiskModel = get_model(module, fresh=True)
    model.fit(X, y)
    path = artifact_path(out_dir, module)
    model.save(path)
    return {
        "module": module.value,
        "path": path,
        "backend": model.backend,
        "backend_active": model._backend_obj is not None,
        "fitted": model.fitted,
        "n_train": len(y),
        "data_source": source,
        "seed": seed,
        "feature_names": list(FEATURE_NAMES[module]),
    }


def train_all(
    out_dir: str, *, n: int | None = DEFAULT_N, seed: int = 0, source: str = "real"
) -> dict[str, Any]:
    """Train + persist all three module models under ``out_dir``.

    Returns a manifest ``dict`` with a per-module ``models`` list, the ``out_dir``
    and the ``seed`` — JSON-serialisable so ``__main__`` can print it and tests can
    introspect it. Writes a ``manifest.json`` next to the artefacts as a record.
    """
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    entries = [train_module(out_dir, m, n=n, seed=seed, source=source) for m in MODULES]
    manifest: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "out_dir": out_dir,
        "seed": seed,
        "n_train": n,
        "data_source": source,
        "models": entries,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True, indent=2)
    return manifest


def load_trained(out_dir: str, module: Module | str) -> RiskModel:
    """Restore the persisted model for ``module`` from ``out_dir``.

    Thin wrapper over :meth:`disastermind.ml.RiskModel.load` at the deterministic
    :func:`artifact_path`. The restored model answers ``predict`` in ``[0, 1]``
    even if its real backend's library is now absent (graceful degradation).
    """
    return RiskModel.load(artifact_path(os.path.abspath(out_dir), module))
