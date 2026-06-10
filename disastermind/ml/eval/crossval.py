"""Blocked cross-validation — proof of generalisation across space AND time.

A single temporal split shows the model works "later"; it does not show it works
*elsewhere* or in *other years*. This module adds the two verification designs
that close that gap (Roberts et al. 2017, "blocked CV for structured data"):

  * **Leave-one-region-out (LORO):** hold out every spatial block (a basin, a
    fire regime, a seismic macro-region) in turn, train on the rest, score on
    the held-out block. Aftershock clusters / shared-basin hydrology never
    straddle the boundary, so the score measures transfer to unseen geography.
  * **Rolling-origin temporal CV:** for each fold, train strictly on years
    ``< origin`` and test on the single year at ``origin``, advancing the origin
    year by year. Every fold respects causality (no future data in training),
    and the per-fold series doubles as the skill-decay curve for drift review.

The functions are generic: rows are ``(x, y, time, region)`` quadruples and the
caller supplies a ``fit(X, y) -> predict`` factory, so the same machinery runs
the earthquake, flood and fire validations. Stdlib only, deterministic.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .metrics import brier_score, roc_auc

#: ``fit(X, y)`` returning a ``predict(X) -> probabilities`` callable.
FitFactory = Callable[
    [list[list[float]], list[int]], Callable[[list[list[float]]], list[float]]
]


@dataclass(frozen=True)
class Fold:
    """Score card for one held-out block (a region or a year)."""

    held_out: str
    n_train: int
    n_test: int
    positives: int
    auc: float
    brier: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "held_out": self.held_out,
            "n_train": self.n_train,
            "n_test": self.n_test,
            "positives": self.positives,
            "auc": self.auc,
            "brier": self.brier,
        }


def _score_fold(
    name: str,
    fit: FitFactory,
    Xtr: list[list[float]],
    ytr: list[int],
    Xte: list[list[float]],
    yte: list[int],
) -> Fold:
    predict = fit(Xtr, ytr)
    p = predict(Xte)
    return Fold(
        held_out=name,
        n_train=len(ytr),
        n_test=len(yte),
        positives=sum(yte),
        auc=roc_auc(yte, p),
        brier=brier_score(yte, p),
    )


def leave_one_region_out(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    regions: Sequence[str],
    fit: FitFactory,
    *,
    min_test: int = 30,
) -> list[Fold]:
    """One fold per distinct region: train on all others, test on the held-out one.

    Regions with fewer than ``min_test`` rows or with single-class labels are
    skipped (their AUC would be undefined/meaningless), but the skip is visible
    to callers because the returned folds name exactly which regions were scored.
    """
    if not (len(X) == len(y) == len(regions)):
        raise ValueError("X / y / regions length mismatch")
    folds: list[Fold] = []
    for region in sorted(set(regions)):
        tr = [i for i, r in enumerate(regions) if r != region]
        te = [i for i, r in enumerate(regions) if r == region]
        yte = [y[i] for i in te]
        if len(te) < min_test or len(set(yte)) < 2:
            continue
        folds.append(
            _score_fold(
                region,
                fit,
                [list(X[i]) for i in tr],
                [y[i] for i in tr],
                [list(X[i]) for i in te],
                yte,
            )
        )
    return folds


def rolling_origin(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    years: Sequence[int],
    fit: FitFactory,
    *,
    min_train_years: int = 2,
    min_test: int = 30,
) -> list[Fold]:
    """One fold per test year: train on all strictly earlier years.

    The first ``min_train_years`` years only ever train. Each fold is named by
    its test year, so the sequence of fold AUCs read in order IS the temporal
    skill-decay curve (consumed by :mod:`disastermind.ml.eval.drift`).
    """
    if not (len(X) == len(y) == len(years)):
        raise ValueError("X / y / years length mismatch")
    ordered = sorted(set(years))
    folds: list[Fold] = []
    for origin in ordered[min_train_years:]:
        tr = [i for i, yr in enumerate(years) if yr < origin]
        te = [i for i, yr in enumerate(years) if yr == origin]
        yte = [y[i] for i in te]
        if len(te) < min_test or len(set(yte)) < 2 or not tr:
            continue
        folds.append(
            _score_fold(
                str(origin),
                fit,
                [list(X[i]) for i in tr],
                [y[i] for i in tr],
                [list(X[i]) for i in te],
                yte,
            )
        )
    return folds


def summarise(folds: Sequence[Fold]) -> dict[str, Any]:
    """Worst/mean/best AUC across folds — the generalisation headline.

    The number that matters for "does it work in Odisha and the Himalayas" is the
    WORST held-out block, not the average; both are reported.
    """
    if not folds:
        return {"folds": 0, "auc_worst": None, "auc_mean": None, "auc_best": None}
    aucs = sorted(f.auc for f in folds)
    return {
        "folds": len(folds),
        "auc_worst": round(aucs[0], 4),
        "auc_mean": round(sum(aucs) / len(aucs), 4),
        "auc_best": round(aucs[-1], 4),
        "per_fold": [f.to_dict() for f in folds],
    }
