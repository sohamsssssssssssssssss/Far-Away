"""disastermind.ml.training — produce the artefacts the ML seam loads.

PRD Step 10. The tier-2 prediction agents consume per-module risk models via
:func:`disastermind.ml.get_model`; this package is the *producer* that fits those
models on REAL historical data and persists them:

  * :mod:`~disastermind.ml.training.real` — training tables derived from the
    committed real fixtures (USGS quakes, GloFAS/ERA5 floods, FPA-FOD/ERA5
    wildfires), columns matching :data:`disastermind.ml.FEATURE_NAMES`. This is
    the production path: NO shipped artefact is fitted on synthetic data.
  * :mod:`~disastermind.ml.training.synthetic` — the legacy controllable-signal
    generator, retained for unit tests only (``source="synthetic"``).
  * :mod:`~disastermind.ml.training.train`     — ``train_all(out_dir)``
    fits+saves all three; ``load_trained(out_dir, module)`` restores one.

Run as a module to train+save all three and print the manifest::

    python -m disastermind.ml.training --out <dir>

Stdlib-only on import + happy path; optional ML libraries are reached lazily
inside the model wrappers, with a deterministic heuristic fallback when absent. No
network, no wall-clock — a given ``seed`` reproduces the same artefacts exactly.
"""
from __future__ import annotations

from .real import make_real_dataset
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
    # real training data (production path)
    "make_real_dataset",
    # synthetic data (test-only)
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
