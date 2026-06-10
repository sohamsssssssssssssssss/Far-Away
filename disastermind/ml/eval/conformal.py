"""Calibrated uncertainty — isotonic recalibration + split-conformal guarantees.

Point probabilities are not enough to call an evacuation; the PRD needs "when it
says 80%, it is right ~80% of the time" *and* a distribution-free guarantee on
how often the system's uncertainty sets are wrong. Two standard, stdlib-friendly
tools provide that:

  * **Isotonic recalibration (PAV).** Fit a monotone step function from raw
    model scores to observed frequencies on a held-out CALIBRATION split (never
    the test split), via the classic pool-adjacent-violators algorithm. Applied
    to test scores it repairs systematic over/under-confidence — the measured
    failure mode here was under-confidence at the low end — and the improvement
    is verified by re-measuring ECE on the untouched test set.
  * **Split (inductive) conformal prediction.** Using nonconformity
    ``1 - p(true class)`` on the calibration split, emit per-row prediction SETS
    that contain the true label with frequency >= 1 - alpha, finite-sample,
    regardless of how miscalibrated the underlying model is (Vovk et al.).
    A row whose set is ``{0, 1}`` is the model saying "I genuinely don't know" —
    operationally a "send a human / gather more data" signal, which is exactly
    the honesty an evacuation decision needs.

Stdlib only, deterministic, split discipline enforced by the API shape: both
tools are FIT on a calibration split and APPLIED elsewhere.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .metrics import calibration_bins, expected_calibration_error


# ----------------------------------------------------------------- isotonic (PAV)
@dataclass(frozen=True)
class IsotonicCalibrator:
    """Monotone score -> probability map fitted by pool-adjacent-violators.

    ``thresholds`` are the (sorted) right-open boundaries of the fitted blocks;
    ``values`` the calibrated probability of each block. Apply with
    :meth:`transform`; persists as plain JSON via :meth:`to_dict`/:meth:`from_dict`
    so a calibrator can ship alongside a model artefact.
    """

    thresholds: tuple[float, ...]
    values: tuple[float, ...]

    def transform_one(self, p: float) -> float:
        if not self.values:
            return p
        return self.values[min(bisect_right(self.thresholds, p), len(self.values) - 1)]

    def transform(self, probs: Sequence[float]) -> list[float]:
        return [self.transform_one(float(p)) for p in probs]

    def to_dict(self) -> dict[str, Any]:
        return {"thresholds": list(self.thresholds), "values": list(self.values)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IsotonicCalibrator:
        return cls(thresholds=tuple(d["thresholds"]), values=tuple(d["values"]))


def fit_isotonic(y_cal: Sequence[int], p_cal: Sequence[float]) -> IsotonicCalibrator:
    """PAV fit on the calibration split: monotone non-decreasing frequencies.

    Sorts rows by raw score, pools adjacent blocks whenever the empirical
    positive rate would decrease, and keys each fitted block by its maximum raw
    score. Exact, O(n log n), no dependencies.
    """
    if len(y_cal) != len(p_cal):
        raise ValueError("y_cal / p_cal length mismatch")
    if not y_cal:
        return IsotonicCalibrator(thresholds=(), values=())
    order = sorted(range(len(p_cal)), key=lambda i: float(p_cal[i]))
    # Each block: [sum_labels, count, max_score]; merge while monotonicity breaks.
    blocks: list[list[float]] = []
    for i in order:
        blocks.append([float(1 if y_cal[i] else 0), 1.0, float(p_cal[i])])
        while len(blocks) > 1 and (
            blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]
        ):
            s, c, m = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
            blocks[-1][2] = m
    return IsotonicCalibrator(
        thresholds=tuple(b[2] for b in blocks[:-1]),
        values=tuple(b[0] / b[1] for b in blocks),
    )


def calibration_report(
    y_test: Sequence[int],
    p_raw: Sequence[float],
    p_calibrated: Sequence[float],
    *,
    n_bins: int = 10,
) -> dict[str, Any]:
    """Before/after reliability on the untouched test split.

    Returns ECE raw vs calibrated plus both reliability tables, so "calibration
    improved" is a measured claim, not an assumption.
    """
    raw_bins = calibration_bins(y_test, p_raw, n_bins=n_bins)
    cal_bins = calibration_bins(y_test, p_calibrated, n_bins=n_bins)
    return {
        "ece_raw": expected_calibration_error(raw_bins),
        "ece_calibrated": expected_calibration_error(cal_bins),
        "reliability_raw": [b.to_dict() for b in raw_bins if b.count],
        "reliability_calibrated": [b.to_dict() for b in cal_bins if b.count],
    }


# ------------------------------------------------------------------ conformal sets
@dataclass(frozen=True)
class ConformalClassifier:
    """Split-conformal binary classifier built from calibration nonconformity.

    Stores the sorted nonconformity scores (``1 - p(true class)``) of the
    calibration split; :meth:`predict_set` emits the label set whose members'
    conformal p-values exceed ``alpha``. Coverage >= 1 - alpha holds by the
    standard exchangeability argument, independent of model calibration.
    """

    scores: tuple[float, ...]  # sorted ascending
    alpha: float

    def _p_value(self, nonconformity: float) -> float:
        """Fraction of calibration scores >= this one (with +1 correction)."""
        n = len(self.scores)
        # bisect on the sorted scores: count of cal scores >= nonconformity
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.scores[mid] < nonconformity:
                lo = mid + 1
            else:
                hi = mid
        return (n - lo + 1) / (n + 1)

    def predict_set(self, p: float) -> tuple[int, ...]:
        """Prediction set for one raw probability ``p`` of the positive class."""
        members: list[int] = []
        # Nonconformity for label 0 is p itself; for label 1 it is 1 - p.
        if self._p_value(float(p)) >= self.alpha:
            members.append(0)
        if self._p_value(1.0 - float(p)) >= self.alpha:
            members.append(1)
        return tuple(members)

    def predict_sets(self, probs: Sequence[float]) -> list[tuple[int, ...]]:
        return [self.predict_set(p) for p in probs]


def fit_conformal(
    y_cal: Sequence[int], p_cal: Sequence[float], *, alpha: float = 0.1
) -> ConformalClassifier:
    """Calibrate a split-conformal classifier at miscoverage level ``alpha``."""
    if len(y_cal) != len(p_cal):
        raise ValueError("y_cal / p_cal length mismatch")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    scores = sorted(
        (1.0 - float(p)) if lab else float(p) for lab, p in zip(y_cal, p_cal)
    )
    return ConformalClassifier(scores=tuple(scores), alpha=alpha)


def coverage_report(
    clf: ConformalClassifier, y_test: Sequence[int], p_test: Sequence[float]
) -> dict[str, Any]:
    """Empirical coverage + efficiency of the conformal sets on the test split.

    ``coverage`` should be >= 1 - alpha (up to finite-sample noise);
    ``singleton_rate`` is the share of rows where the model commits to one label
    (the useful predictions); ``abstain_rate`` is the {0,1} "don't know" share.
    """
    sets = clf.predict_sets(p_test)
    n = len(sets) or 1
    covered = sum(1 for s, lab in zip(sets, y_test) if (1 if lab else 0) in s)
    singles = sum(1 for s in sets if len(s) == 1)
    empties = sum(1 for s in sets if not s)
    return {
        "alpha": clf.alpha,
        "target_coverage": 1.0 - clf.alpha,
        "coverage": covered / n,
        "singleton_rate": singles / n,
        "abstain_rate": (n - singles - empties) / n,
        "empty_rate": empties / n,
        "n": len(sets),
    }
