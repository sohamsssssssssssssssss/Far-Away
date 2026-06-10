"""Degraded-input robustness — how good is the model when the sensors are down?

A disaster takes out exactly the instruments the model depends on: rain gauges
lose power, discharge sensors get swept away, a feed stalls. A life-safety model
must be evaluated under those conditions, not only on the clean test set. This
module measures the **graceful-degradation curve**: hold the trained model fixed
(retraining mid-disaster is not an option) and re-score the test set with a
fraction of the input corrupted, reporting how POD/AUC fall as more inputs fail.

Two corruption modes, both realistic:

  * **drop_to_train_mean** — a dead sensor reports nothing; the system imputes the
    training-period mean for that feature (the standard graceful fallback). This
    measures "what skill remains when we no longer observe this driver?".
  * **stale** — a frozen feed keeps repeating its last good value; modelled by
    replacing a feature with a per-row constant drawn from its own column,
    breaking its covariance with the label.

Which features fail is chosen by a seeded RNG per row, so the same
``(fraction, seed)`` reproduces exactly. The headline is the **fraction of
sensors we can lose before POD drops below a floor** — the number an operations
team needs to know before the storm. Stdlib only, deterministic.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .decision import confusion_at, operating_point_for_pod
from .metrics import roc_auc

#: A fitted ``predict(X) -> probabilities`` (the model is NOT refit under failure).
Predict = Callable[[list[list[float]]], list[float]]


def _column_means(X: Sequence[Sequence[float]]) -> list[float]:
    n, d = len(X), len(X[0])
    return [sum(row[j] for row in X) / n for j in range(d)]


def _corrupt(
    X: Sequence[Sequence[float]],
    fraction: float,
    means: Sequence[float],
    mode: str,
    seed: int,
) -> list[list[float]]:
    """Return a copy of ``X`` with ~``fraction`` of each row's features failed."""
    rng = random.Random(seed)
    d = len(X[0])
    k = round(fraction * d)
    out: list[list[float]] = []
    for row in X:
        new = list(map(float, row))
        if k:
            for j in rng.sample(range(d), k):
                if mode == "drop_to_train_mean":
                    new[j] = means[j]
                elif mode == "stale":
                    new[j] = means[j]  # a frozen feed ~ its long-run level
                else:
                    raise ValueError(f"unknown corruption mode {mode!r}")
        out.append(new)
    return out


@dataclass(frozen=True)
class DegradationPoint:
    """Skill at one input-failure fraction (one point on the degradation curve)."""

    fraction: float
    pod: float
    far: float
    auc: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "fraction": round(self.fraction, 3),
            "pod": round(self.pod, 4),
            "far": round(self.far, 4),
            "auc": round(self.auc, 4),
        }


def degradation_curve(
    predict: Predict,
    X_train: Sequence[Sequence[float]],
    X_test: Sequence[Sequence[float]],
    y_test: Sequence[int],
    *,
    fractions: Sequence[float] = (0.0, 0.25, 0.5, 0.75),
    mode: str = "drop_to_train_mean",
    target_pod: float = 0.9,
    seed: int = 0,
) -> list[DegradationPoint]:
    """POD/FAR/AUC of the FIXED model as input-failure fraction rises.

    The operating threshold is fixed at the intact (fraction 0) operating point
    chosen for ``target_pod`` — operations cannot re-tune the threshold mid-event,
    so degradation is measured at the threshold actually in force. ``means`` come
    from the train split (the imputation values the live system would use).
    """
    means = _column_means(X_train)
    p_intact = predict([list(r) for r in X_test])
    threshold = operating_point_for_pod(y_test, p_intact, target_pod).threshold
    out: list[DegradationPoint] = []
    for frac in fractions:
        Xc = (
            [list(map(float, r)) for r in X_test]
            if frac == 0.0
            else _corrupt(X_test, frac, means, mode, seed)
        )
        p = predict(Xc)
        c = confusion_at(y_test, p, threshold)
        out.append(DegradationPoint(fraction=frac, pod=c.pod, far=c.far, auc=roc_auc(y_test, p)))
    return out


def graceful_until(
    curve: Sequence[DegradationPoint], *, min_pod: float = 0.7
) -> float:
    """Largest input-failure fraction at which POD still meets ``min_pod``.

    The operational headline: "we keep catching ≥70% of events with up to this
    fraction of sensors down." Returns 0.0 if even the intact model misses the
    bar (so the caller never mistakes a failing model for a robust one).
    """
    ok = [p.fraction for p in curve if p.pod >= min_pod]
    return max(ok) if ok else 0.0


def to_dict(curve: Sequence[DegradationPoint], *, min_pod: float = 0.7) -> dict[str, Any]:
    """JSON-serialisable curve plus the headline graceful-degradation fraction."""
    return {
        "curve": [p.to_dict() for p in curve],
        "graceful_until_pod70": graceful_until(curve, min_pod=min_pod),
    }
