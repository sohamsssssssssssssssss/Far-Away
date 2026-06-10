"""Drift detection + retraining policy — proof the model doesn't silently rot.

Operational ML for hazards must assume the world moves: instrument networks
densify, climate shifts the rainfall distribution, building stock changes. This
module provides the three pieces a monitored pipeline needs, all stdlib:

  * **Population Stability Index (PSI)** per feature between a reference window
    (training data) and a live window, on reference-decile bins — the standard
    industry drift score (rule of thumb: <0.10 stable, 0.10-0.25 watch,
    >=0.25 drifted).
  * **Two-sample Kolmogorov-Smirnov statistic** per feature as a
    binning-free cross-check (D plus the classic asymptotic p-value).
  * **A retraining trigger** that turns those numbers + the rolling-origin decay
    curve (:func:`disastermind.ml.eval.crossval.rolling_origin`) into an
    explicit, auditable decision: retrain when any feature drifts past the PSI
    threshold or when held-out skill decays more than ``max_auc_drop`` from its
    historical mean. The decision object says WHICH signal fired.

The decay evidence itself comes from rolling-origin folds — "trained through
year Y, scored on year Y+1" repeated across the catalog — which is exactly the
"does skill hold years after training?" question, answered with real data.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .crossval import Fold

#: PSI rule-of-thumb boundaries (industry convention).
PSI_WATCH = 0.10
PSI_DRIFTED = 0.25


# ------------------------------------------------------------------------ PSI / KS
def psi(reference: Sequence[float], live: Sequence[float], *, n_bins: int = 10) -> float:
    """Population Stability Index of ``live`` against ``reference`` deciles.

    Bins are the reference distribution's quantile edges, so PSI asks "how
    differently does live mass fall across the reference's own deciles?".
    Empty-bin proportions are floored at 1e-4 (standard practice) so the score
    stays finite when live data vacates a bin entirely.
    """
    ref = sorted(float(v) for v in reference)
    if not ref or not live:
        raise ValueError("reference and live must be non-empty")
    edges = [ref[int(k * len(ref) / n_bins)] for k in range(1, n_bins)]

    def _proportions(values: Sequence[float]) -> list[float]:
        counts = [0] * n_bins
        for v in values:
            b = 0
            while b < len(edges) and float(v) > edges[b]:
                b += 1
            counts[b] += 1
        total = len(values)
        return [max(c / total, 1e-4) for c in counts]

    p_ref = _proportions(ref)
    p_live = _proportions([float(v) for v in live])
    return sum((pl - pr) * math.log(pl / pr) for pr, pl in zip(p_ref, p_live))


def ks_statistic(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """Two-sample KS ``(D, p_value)`` with the asymptotic Kolmogorov p-value."""
    xs = sorted(float(v) for v in a)
    ys = sorted(float(v) for v in b)
    if not xs or not ys:
        raise ValueError("both samples must be non-empty")
    d = 0.0
    ii = jj = 0
    while ii < len(xs) and jj < len(ys):
        if xs[ii] <= ys[jj]:
            ii += 1
        else:
            jj += 1
        d = max(d, abs(ii / len(xs) - jj / len(ys)))
    d = max(d, abs(1.0 - jj / len(ys)), abs(ii / len(xs) - 1.0))
    # Asymptotic Kolmogorov distribution: p = 2 * sum (-1)^{k-1} exp(-2 k^2 t^2).
    en = math.sqrt(len(xs) * len(ys) / (len(xs) + len(ys)))
    t = (en + 0.12 + 0.11 / en) * d
    p = 0.0
    for k in range(1, 101):
        term = 2.0 * ((-1.0) ** (k - 1)) * math.exp(-2.0 * (k * t) ** 2)
        p += term
        if abs(term) < 1e-10:
            break
    return d, min(max(p, 0.0), 1.0)


@dataclass(frozen=True)
class FeatureDrift:
    """Drift verdict for one feature column."""

    feature: str
    psi: float
    ks_d: float
    ks_p: float

    @property
    def status(self) -> str:
        if self.psi >= PSI_DRIFTED:
            return "drifted"
        if self.psi >= PSI_WATCH:
            return "watch"
        return "stable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "psi": self.psi,
            "ks_d": self.ks_d,
            "ks_p": self.ks_p,
            "status": self.status,
        }


def feature_drift(
    feature_names: Sequence[str],
    X_reference: Sequence[Sequence[float]],
    X_live: Sequence[Sequence[float]],
) -> list[FeatureDrift]:
    """Per-feature PSI + KS between training-time and live feature matrices."""
    out: list[FeatureDrift] = []
    for j, name in enumerate(feature_names):
        ref = [row[j] for row in X_reference]
        live = [row[j] for row in X_live]
        d, p = ks_statistic(ref, live)
        out.append(FeatureDrift(feature=name, psi=psi(ref, live), ks_d=d, ks_p=p))
    return out


# ---------------------------------------------------------------- retrain decision
@dataclass(frozen=True)
class RetrainDecision:
    """An auditable retrain/hold decision with the signals that produced it."""

    retrain: bool
    reasons: tuple[str, ...]
    drifted_features: tuple[str, ...]
    auc_recent: float | None
    auc_historic_mean: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrain": self.retrain,
            "reasons": list(self.reasons),
            "drifted_features": list(self.drifted_features),
            "auc_recent": self.auc_recent,
            "auc_historic_mean": self.auc_historic_mean,
        }


def retrain_decision(
    drifts: Sequence[FeatureDrift],
    decay_folds: Sequence[Fold],
    *,
    psi_threshold: float = PSI_DRIFTED,
    max_auc_drop: float = 0.05,
) -> RetrainDecision:
    """Combine drift scores and the decay curve into an explicit trigger.

    Fires when (a) any feature's PSI crosses ``psi_threshold``, or (b) the most
    recent rolling-origin fold's AUC sits more than ``max_auc_drop`` below the
    mean of the earlier folds (skill decay). With fewer than two decay folds the
    decay test abstains — absence of evidence is not treated as stability.
    """
    reasons: list[str] = []
    drifted = tuple(d.feature for d in drifts if d.psi >= psi_threshold)
    if drifted:
        reasons.append(f"feature drift: PSI >= {psi_threshold} for {', '.join(drifted)}")

    recent = historic = None
    if len(decay_folds) >= 2:
        recent = decay_folds[-1].auc
        earlier = [f.auc for f in decay_folds[:-1]]
        historic = sum(earlier) / len(earlier)
        if recent < historic - max_auc_drop:
            reasons.append(
                f"skill decay: latest fold AUC {recent:.3f} vs historic mean {historic:.3f}"
            )
    return RetrainDecision(
        retrain=bool(reasons),
        reasons=tuple(reasons),
        drifted_features=drifted,
        auc_recent=recent,
        auc_historic_mean=historic,
    )
