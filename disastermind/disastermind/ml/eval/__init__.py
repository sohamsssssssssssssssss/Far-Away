"""disastermind.ml.eval — model evaluation, backtesting and model cards.

PRD Step 3 ("validated accuracy") + Step 9/10 (explainability, honest provenance).
This package is the *judge* of the ML seam: it scores the per-module risk models
on held-out data and documents them, all in pure stdlib with no network and no
optional ML dependency required.

  * :mod:`~disastermind.ml.eval.metrics` — :func:`evaluate` ``(y_true, y_prob) ->``
    :class:`Metrics` with rank-based AUC, Brier score, accuracy@threshold and
    calibration bins.
  * :mod:`~disastermind.ml.eval.backtest` — :func:`backtest` builds a train/test
    split per module A/B/C, trains via :func:`disastermind.ml.get_model` + ``fit``,
    evaluates on held-out data and returns/writes the results.
  * :mod:`~disastermind.ml.eval.cards` — :func:`model_card` /
    :func:`to_markdown` document features, training size, metrics, intended use
    and limitations ("trained on synthetic data — not validated on real events").

Run ``python -m disastermind.ml.eval`` to backtest all modules and print the
result (optionally writing artefacts to ``--out``).
"""
from __future__ import annotations

from .backtest import (
    DEFAULT_N,
    DEFAULT_TEST_FRACTION,
    MODULES,
    RESULT_FORMAT,
    backtest,
    backtest_module,
    train_test_split,
)
from .cards import model_card, to_markdown
from .metrics import (
    CalibrationBin,
    Metrics,
    accuracy_at,
    brier_score,
    calibration_bins,
    evaluate,
    expected_calibration_error,
    roc_auc,
)

__all__ = [
    # metrics
    "Metrics",
    "CalibrationBin",
    "evaluate",
    "roc_auc",
    "brier_score",
    "accuracy_at",
    "calibration_bins",
    "expected_calibration_error",
    # backtest
    "backtest",
    "backtest_module",
    "train_test_split",
    "MODULES",
    "RESULT_FORMAT",
    "DEFAULT_N",
    "DEFAULT_TEST_FRACTION",
    # cards
    "model_card",
    "to_markdown",
]
