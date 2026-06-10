"""Backtest harness — train each module model and score it on held-out data.

PRD Step 3 ("validated accuracy"). For each module A/B/C this:

  1. builds a deterministic synthetic dataset via
     :func:`disastermind.ml.training.make_dataset`,
  2. splits it into train / test by a seeded permutation (no leakage: the test
     rows never enter ``fit``),
  3. obtains the module's wrapper via :func:`disastermind.ml.get_model`
     (``fresh=True`` so a cached/previously-fitted instance is never reused),
  4. ``fit``s on the train split,
  5. ``predict``s on the held-out test split and scores the predictions with
     :func:`disastermind.ml.eval.metrics.evaluate`.

Labels are continuous risk in ``[0, 1]``; :func:`evaluate` binarises them at 0.5
so the metrics are genuine *classification* metrics. The whole thing is stdlib
only and fully deterministic in ``seed``: with no optional ML library installed
the wrappers fall back to their deterministic heuristic, so a backtest still runs
end-to-end and still produces sensible AUC/Brier numbers (the synthetic labels and
the heuristic share the same monotone direction).

:func:`backtest` returns a JSON-serialisable result and, when ``out_dir`` is given,
writes ``backtest.json`` plus a per-module ``card_<M>.md`` model card.
"""
from __future__ import annotations

import json
import os
import random
from collections.abc import Sequence
from typing import Any

from ...core.contracts import Module
from ..registry import get_model
from ..training import make_dataset
from .cards import model_card, to_markdown
from .metrics import Metrics, evaluate

#: Modules backtested, in stable A/B/C order.
MODULES: tuple[Module, ...] = (
    Module.CYCLONE_FLOOD,
    Module.EARTHQUAKE,
    Module.FIRE_COLLAPSE,
)

#: Result format tag (parallels the training manifest's "disastermind.ml.training/1").
RESULT_FORMAT = "disastermind.ml.eval/1"

#: Default number of synthetic rows generated per module before the split.
DEFAULT_N = 512

#: Default fraction of rows held out for the test split.
DEFAULT_TEST_FRACTION = 0.25


def train_test_split(
    X: Sequence[Sequence[float]],
    y: Sequence[float],
    *,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    seed: int = 0,
) -> tuple[list[list[float]], list[float], list[list[float]], list[float]]:
    """Deterministically split ``(X, y)`` into ``(X_tr, y_tr, X_te, y_te)``.

    A seeded :class:`random.Random` permutes the row indices, then the first
    ``test_fraction`` go to the test split. Seeded, so the split reproduces; the
    permutation guarantees the (ordered) synthetic rows are shuffled before the
    cut, avoiding any ordering artefact.
    """
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be in (0, 1)")
    n = len(X)
    if n != len(y):
        raise ValueError("X and y length mismatch")
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    n_test = max(1, int(round(n * test_fraction))) if n else 0
    test_idx = set(idx[:n_test])
    X_tr, y_tr, X_te, y_te = [], [], [], []
    for i in range(n):
        row = [float(v) for v in X[i]]
        if i in test_idx:
            X_te.append(row)
            y_te.append(float(y[i]))
        else:
            X_tr.append(row)
            y_tr.append(float(y[i]))
    return X_tr, y_tr, X_te, y_te


def backtest_module(
    module: Module | str,
    *,
    n: int = DEFAULT_N,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    seed: int = 0,
) -> dict[str, Any]:
    """Train + held-out-evaluate one module; return its result entry.

    The entry carries the fitted model's ``backend`` (and whether its real backend
    was actually active), the train/test sizes, the full :class:`Metrics` and a
    rendered model card — everything needed to judge and document the model.
    """
    module = module if isinstance(module, Module) else Module(module)
    X, y = make_dataset(module, n=n, seed=seed)
    X_tr, y_tr, X_te, y_te = train_test_split(
        X, y, test_fraction=test_fraction, seed=seed
    )
    model = get_model(module, fresh=True)
    model.fit(X_tr, y_tr)
    preds = model.predict(X_te)
    metrics: Metrics = evaluate(y_te, preds)
    card = model_card(module, model, metrics, n_train=len(X_tr))
    return {
        "module": module.value,
        "backend": model.backend,
        "backend_active": model._backend_obj is not None,
        "fitted": model.fitted,
        "n_train": len(X_tr),
        "n_test": len(X_te),
        "seed": seed,
        "metrics": metrics.to_dict(),
        "card": card,
    }


def backtest(
    out_dir: str | None = None,
    *,
    n: int = DEFAULT_N,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    seed: int = 0,
) -> dict[str, Any]:
    """Backtest every module (A/B/C) and return a JSON-serialisable result.

    When ``out_dir`` is given the result is written to ``backtest.json`` and each
    module's card is written to ``card_<M>.md`` under that directory (created if
    needed). With no optional ML library present every wrapper uses its
    deterministic heuristic, so this still runs offline and reproduces exactly for
    a given ``seed``.
    """
    entries = [
        backtest_module(m, n=n, test_fraction=test_fraction, seed=seed)
        for m in MODULES
    ]
    result: dict[str, Any] = {
        "format": RESULT_FORMAT,
        "seed": seed,
        "n": n,
        "test_fraction": test_fraction,
        "modules": entries,
    }
    if out_dir is not None:
        out_dir = os.path.abspath(out_dir)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "backtest.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, sort_keys=True, indent=2)
        for entry in entries:
            md = to_markdown(entry["card"])
            card_path = os.path.join(out_dir, f"card_{entry['module']}.md")
            with open(card_path, "w", encoding="utf-8") as fh:
                fh.write(md)
        result["out_dir"] = out_dir
    return result
