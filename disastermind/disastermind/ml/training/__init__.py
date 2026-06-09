"""disastermind.ml.training — produce the artefacts the ML seam loads.

PRD Step 10. The tier-2 prediction agents consume per-module risk models via
:func:`disastermind.ml.get_model`; this package is the *producer* that fits those
models on deterministic synthetic data and persists them:

  * :mod:`~disastermind.ml.training.synthetic` — reproducible labelled ``(X, y)``
    per module A/B/C, columns matching :data:`disastermind.ml.FEATURE_NAMES`.
  * :mod:`~disastermind.ml.training.train`     — ``train_all(out_dir, seed=0)``
    fits+saves all three; ``load_trained(out_dir, module)`` restores one.

Run as a module to train+save all three and print the manifest::

    python -m disastermind.ml.training --out <dir>

Stdlib-only on import + happy path; optional ML libraries are reached lazily
inside the model wrappers, with a deterministic heuristic fallback when absent. No
network, no wall-clock — a given ``seed`` reproduces the same artefacts exactly.
"""
from __future__ import annotations

from .synthetic import (
    extreme_rows,
    label_for,
    make_dataset,
)
from .train import (
    DEFAULT_N,
    MANIFEST_FORMAT,
    MODULES,
    artifact_path,
    load_trained,
    train_all,
    train_module,
)

__all__ = [
    # synthetic data
    "make_dataset",
    "label_for",
    "extreme_rows",
    # training pipeline
    "train_all",
    "train_module",
    "load_trained",
    "artifact_path",
    "MODULES",
    "DEFAULT_N",
    "MANIFEST_FORMAT",
]
