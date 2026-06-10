"""Decision-point metrics — POD/FAR at the threshold we would actually act on.

For life-safety forecasting, AUC is the wrong headline: dispatch decisions happen
at ONE operating threshold, and what matters there is

  * **POD** (probability of detection, = recall on events): of the real damaging
    events, how many did we catch?
  * **FAR** (false-alarm ratio): of the alarms we raised, how many were false?
    (Distinct from POFD, the false-alarm *rate* over non-events — both reported.)
  * **CSI** (critical success index / threat score) and **HSS** (Heidke skill
    score vs chance) — the standard operational forecast-verification scores
    (Jolliffe & Stephenson; used by IMD/NOAA verification practice).
  * **frequency bias** — alarms divided by events (>1 = over-warning).

This module computes those from binary labels + predicted probabilities at any
threshold, *selects* the operating point for a target POD (the "we must catch
95% of damaging events" constraint), and makes the miss/false-alarm trade-off
explicit in money-like cost units so the chosen threshold is an articulated
policy, not an accident of 0.5.

Stdlib only, deterministic, no optional dependencies — same contract as
:mod:`disastermind.ml.eval.metrics`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .metrics import _coerce


# --------------------------------------------------------------------------- counts
@dataclass(frozen=True)
class Confusion:
    """2x2 contingency table at one threshold (hits/misses/false alarms)."""

    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def pod(self) -> float:
        """Probability of detection (hit rate, recall): tp / (tp + fn)."""
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def far(self) -> float:
        """False-alarm RATIO: fp / (tp + fp) — share of alarms that were false."""
        d = self.tp + self.fp
        return self.fp / d if d else 0.0

    @property
    def pofd(self) -> float:
        """False-alarm RATE (prob. of false detection): fp / (fp + tn)."""
        d = self.fp + self.tn
        return self.fp / d if d else 0.0

    @property
    def csi(self) -> float:
        """Critical success index (threat score): tp / (tp + fp + fn)."""
        d = self.tp + self.fp + self.fn
        return self.tp / d if d else 0.0

    @property
    def bias(self) -> float:
        """Frequency bias: (tp + fp) / (tp + fn); >1 means over-alerting."""
        d = self.tp + self.fn
        return (self.tp + self.fp) / d if d else 0.0

    @property
    def hss(self) -> float:
        """Heidke skill score vs random chance, in (-inf, 1]; 0 = no skill."""
        num = 2.0 * (self.tp * self.tn - self.fp * self.fn)
        den = (self.tp + self.fn) * (self.fn + self.tn) + (self.tp + self.fp) * (self.fp + self.tn)
        return num / den if den else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "pod": self.pod,
            "far": self.far,
            "pofd": self.pofd,
            "csi": self.csi,
            "bias": self.bias,
            "hss": self.hss,
        }


def confusion_at(
    y_true: Sequence[Any], y_prob: Sequence[Any], threshold: float
) -> Confusion:
    """Contingency counts with alarms defined as ``p >= threshold``."""
    labels, probs = _coerce(y_true, y_prob)
    tp = fp = fn = tn = 0
    for lab, p in zip(labels, probs):
        alarm = p >= threshold
        if alarm and lab:
            tp += 1
        elif alarm:
            fp += 1
        elif lab:
            fn += 1
        else:
            tn += 1
    return Confusion(threshold=float(threshold), tp=tp, fp=fp, fn=fn, tn=tn)


# ----------------------------------------------------------------- operating point
def operating_point_for_pod(
    y_true: Sequence[Any],
    y_prob: Sequence[Any],
    target_pod: float = 0.95,
) -> Confusion:
    """Highest threshold whose POD still meets ``target_pod`` on this data.

    "Catch at least 95% of damaging events, with the fewest false alarms that
    allows." Scans the distinct predicted probabilities from high to low and
    returns the confusion at the highest threshold satisfying the constraint;
    falls back to alarming on everything (threshold 0) if even that is needed.
    """
    if not 0.0 < target_pod <= 1.0:
        raise ValueError("target_pod must be in (0, 1]")
    labels, probs = _coerce(y_true, y_prob)
    best: Confusion | None = None
    for t in sorted(set(probs), reverse=True):
        c = confusion_at(labels, probs, t)
        if c.pod >= target_pod:
            best = c
            break
    return best if best is not None else confusion_at(labels, probs, 0.0)


def operating_point_min_cost(
    y_true: Sequence[Any],
    y_prob: Sequence[Any],
    *,
    miss_cost: float,
    false_alarm_cost: float,
) -> tuple[Confusion, float]:
    """Threshold minimising explicit expected cost; returns ``(confusion, cost)``.

    ``miss_cost`` is the cost of failing to warn before a real damaging event
    (lives at risk dominates); ``false_alarm_cost`` is the cost of a needless
    activation (evacuation fatigue, crew time). Scanning every distinct score
    keeps the policy exact rather than grid-approximate. Total cost is
    ``fn * miss_cost + fp * false_alarm_cost``.
    """
    if miss_cost < 0 or false_alarm_cost < 0:
        raise ValueError("costs must be non-negative")
    labels, probs = _coerce(y_true, y_prob)
    candidates = sorted(set(probs), reverse=True) + [0.0]
    best_c: Confusion | None = None
    best_cost = float("inf")
    for t in candidates:
        c = confusion_at(labels, probs, t)
        cost = c.fn * miss_cost + c.fp * false_alarm_cost
        if cost < best_cost:
            best_c, best_cost = c, cost
    assert best_c is not None  # candidates is never empty
    return best_c, best_cost


# --------------------------------------------------------------------------- report
def decision_report(
    y_true: Sequence[Any],
    y_prob: Sequence[Any],
    *,
    target_pod: float = 0.95,
    miss_cost: float = 100.0,
    false_alarm_cost: float = 1.0,
) -> dict[str, Any]:
    """One JSON-serialisable bundle answering "what happens when we dispatch?".

    Reports the confusion at the POD-constrained operating point AND at the
    cost-minimal point, with the explicit cost assumptions, so the threshold
    choice is auditable. The default 100:1 miss:false-alarm ratio encodes
    "missing a damaging event is two orders of magnitude worse than a needless
    activation" — callers must override with their jurisdiction's real costs.
    """
    pod_point = operating_point_for_pod(y_true, y_prob, target_pod)
    cost_point, cost = operating_point_min_cost(
        y_true, y_prob, miss_cost=miss_cost, false_alarm_cost=false_alarm_cost
    )
    return {
        "target_pod": target_pod,
        "at_target_pod": pod_point.to_dict(),
        "cost_assumptions": {"miss": miss_cost, "false_alarm": false_alarm_cost},
        "min_cost": {"point": cost_point.to_dict(), "total_cost": cost},
    }
