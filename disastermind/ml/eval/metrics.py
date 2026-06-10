"""Pure-stdlib classification metrics for the risk models (PRD Step 3).

This module answers the "validated accuracy" gap directly: given ground-truth
binary labels ``y_true`` and the model's predicted probabilities ``y_prob`` it
computes the metrics one needs to *believe* a probability of risk —

  * **ROC AUC** via the rank-based Mann-Whitney U identity (no integration, no
    sklearn): AUC equals the probability that a random positive scores above a
    random negative, with tied scores credited 0.5. This is exact, handles ties,
    and is ``O(n log n)``.
  * **Brier score** — mean squared error of the probability against the label,
    the standard proper scoring rule for probabilistic forecasts.
  * **accuracy@threshold** — fraction correct once probabilities are thresholded
    (default 0.5), the headline number stakeholders ask for.
  * **calibration bins** — equal-width probability bins, each carrying its count,
    mean predicted probability and observed positive rate, so over/under-confidence
    is visible (a well-calibrated model has ``mean_pred ≈ observed`` per bin).

Everything is computed from first principles with the standard library only:
no numpy, no sklearn, no network. Inputs are plain sequences of numbers; the
result is a frozen :class:`Metrics` dataclass that is JSON-serialisable via
:meth:`Metrics.to_dict`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- helpers
def _coerce(y_true: Sequence[Any], y_prob: Sequence[Any]) -> tuple[list[int], list[float]]:
    """Validate + coerce inputs to ``(binary labels, float probabilities)``.

    Labels are coerced to ``{0, 1}`` (any truthy/>=0.5 value -> 1) and probabilities
    to ``float``. Raises ``ValueError`` on length mismatch so a caller never silently
    evaluates misaligned arrays.
    """
    yt = list(y_true)
    yp = list(y_prob)
    if len(yt) != len(yp):
        raise ValueError(f"y_true/y_prob length mismatch: {len(yt)} != {len(yp)}")
    labels = [1 if float(v) >= 0.5 else 0 for v in yt]
    probs = [float(v) for v in yp]
    return labels, probs


def roc_auc(y_true: Sequence[Any], y_prob: Sequence[Any]) -> float:
    """Rank-based ROC AUC (Mann-Whitney U), ties credited 0.5.

    Returns ``0.5`` for the degenerate single-class case (AUC is undefined when
    every label is the same — 0.5 is the no-skill convention). Exact for ties:
    positives and negatives sharing a score split the comparison evenly.
    """
    labels, probs = _coerce(y_true, y_prob)
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5

    # Rank by score (average ranks for ties), then sum positive ranks.
    order = sorted(range(len(probs)), key=lambda i: probs[i])
    ranks = [0.0] * len(probs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and probs[order[j + 1]] == probs[order[i]]:
            j += 1
        # Indices i..j share a score; assign them the average rank (1-based).
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1

    sum_pos_ranks = sum(r for r, lab in zip(ranks, labels) if lab == 1)
    # Mann-Whitney U for positives, then normalise to AUC.
    u_pos = sum_pos_ranks - n_pos * (n_pos + 1) / 2.0
    return u_pos / (n_pos * n_neg)


def brier_score(y_true: Sequence[Any], y_prob: Sequence[Any]) -> float:
    """Mean squared error of probability vs label (the Brier score)."""
    labels, probs = _coerce(y_true, y_prob)
    if not labels:
        return 0.0
    return sum((p - lab) ** 2 for p, lab in zip(probs, labels)) / len(labels)


def accuracy_at(
    y_true: Sequence[Any], y_prob: Sequence[Any], threshold: float = 0.5
) -> float:
    """Fraction of rows whose thresholded prediction matches the label."""
    labels, probs = _coerce(y_true, y_prob)
    if not labels:
        return 0.0
    correct = sum(1 for p, lab in zip(probs, labels) if (1 if p >= threshold else 0) == lab)
    return correct / len(labels)


@dataclass(frozen=True)
class CalibrationBin:
    """One equal-width probability bin of a calibration (reliability) curve."""

    lower: float
    upper: float
    count: int
    mean_pred: float
    observed: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "lower": self.lower,
            "upper": self.upper,
            "count": self.count,
            "mean_pred": self.mean_pred,
            "observed": self.observed,
        }


def calibration_bins(
    y_true: Sequence[Any], y_prob: Sequence[Any], n_bins: int = 10
) -> list[CalibrationBin]:
    """Equal-width reliability bins over ``[0, 1]``.

    Each bin reports its half-open range ``[lower, upper)`` (the last bin is closed
    so ``p == 1.0`` lands in it), the number of rows that fell in it, the mean
    predicted probability and the observed positive rate. Empty bins are reported
    with ``count == 0`` and ``mean_pred == observed == 0.0`` so the bins always
    tile ``[0, 1]`` and their counts sum to ``len(y_true)``.
    """
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    labels, probs = _coerce(y_true, y_prob)
    width = 1.0 / n_bins
    sums = [0.0] * n_bins
    pos = [0] * n_bins
    cnt = [0] * n_bins
    for p, lab in zip(probs, labels):
        pc = 0.0 if p < 0.0 else 1.0 if p > 1.0 else p
        idx = int(pc / width)
        if idx >= n_bins:  # pc == 1.0 -> last bin
            idx = n_bins - 1
        sums[idx] += pc
        pos[idx] += lab
        cnt[idx] += 1
    bins: list[CalibrationBin] = []
    for b in range(n_bins):
        c = cnt[b]
        bins.append(
            CalibrationBin(
                lower=round(b * width, 12),
                upper=round((b + 1) * width, 12),
                count=c,
                mean_pred=(sums[b] / c) if c else 0.0,
                observed=(pos[b] / c) if c else 0.0,
            )
        )
    return bins


def expected_calibration_error(bins: Sequence[CalibrationBin]) -> float:
    """Count-weighted mean ``|mean_pred - observed|`` across non-empty bins (ECE)."""
    total = sum(b.count for b in bins)
    if total == 0:
        return 0.0
    return sum(b.count * abs(b.mean_pred - b.observed) for b in bins) / total


# --------------------------------------------------------------------------- result
@dataclass(frozen=True)
class Metrics:
    """Bundle of evaluation metrics for one model on one held-out split."""

    n: int
    positives: int
    auc: float
    brier: float
    accuracy: float
    threshold: float
    ece: float
    calibration: tuple[CalibrationBin, ...] = field(default_factory=tuple)

    @property
    def prevalence(self) -> float:
        """Observed positive rate in the evaluated split."""
        return (self.positives / self.n) if self.n else 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view (calibration bins flattened to dicts)."""
        return {
            "n": self.n,
            "positives": self.positives,
            "prevalence": self.prevalence,
            "auc": self.auc,
            "brier": self.brier,
            "accuracy": self.accuracy,
            "threshold": self.threshold,
            "ece": self.ece,
            "calibration": [b.to_dict() for b in self.calibration],
        }


def evaluate(
    y_true: Sequence[Any],
    y_prob: Sequence[Any],
    *,
    threshold: float = 0.5,
    n_bins: int = 10,
) -> Metrics:
    """Compute all metrics for ``(y_true, y_prob)`` and return a :class:`Metrics`.

    ``y_true`` may be continuous risk in ``[0, 1]`` (it is binarised at 0.5) or
    already binary; ``y_prob`` is the model's predicted probability per row. The
    evaluation is pure stdlib and fully deterministic.
    """
    labels, probs = _coerce(y_true, y_prob)
    bins = calibration_bins(labels, probs, n_bins=n_bins)
    return Metrics(
        n=len(labels),
        positives=sum(labels),
        auc=roc_auc(labels, probs),
        brier=brier_score(labels, probs),
        accuracy=accuracy_at(labels, probs, threshold=threshold),
        threshold=threshold,
        ece=expected_calibration_error(bins),
        calibration=tuple(bins),
    )
