"""Paired-bootstrap significance tests — "better than the baseline" with a p-value.

Beating the operational incumbent by 0.003 AUC on one split is noise; a "10"
claims superiority with statistical evidence. This module implements the
standard paired bootstrap over test rows (Efron resampling, both models scored
on the SAME resample so the comparison is paired) for any scalar metric, and
ships ready-made comparisons for AUC (higher better) and Brier (lower better).

Outputs per comparison:

  * the observed metric for model and baseline on the full test set,
  * the mean and a percentile confidence interval of the paired delta,
  * a one-sided bootstrap p-value for "model is NOT better" (small p => the
    improvement survives resampling noise).

Deterministic: a caller-supplied seed feeds one ``random.Random``; no global RNG,
no wall clock. Stdlib only.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .metrics import brier_score, roc_auc


@dataclass(frozen=True)
class PairedComparison:
    """Result of one paired-bootstrap model-vs-baseline comparison."""

    metric: str
    higher_is_better: bool
    model_score: float
    baseline_score: float
    delta_mean: float
    ci_low: float
    ci_high: float
    p_value: float
    n_boot: int

    @property
    def significant(self) -> bool:
        """True when the improvement is significant at the 5% level."""
        return self.p_value < 0.05

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "higher_is_better": self.higher_is_better,
            "model": self.model_score,
            "baseline": self.baseline_score,
            "delta_mean": self.delta_mean,
            "delta_ci95": [self.ci_low, self.ci_high],
            "p_value": self.p_value,
            "significant_at_5pct": self.significant,
            "n_boot": self.n_boot,
        }


def paired_bootstrap(
    y_true: Sequence[Any],
    p_model: Sequence[float],
    p_baseline: Sequence[float],
    *,
    metric: Callable[[Sequence[Any], Sequence[float]], float],
    metric_name: str,
    higher_is_better: bool = True,
    n_boot: int = 1000,
    seed: int = 0,
) -> PairedComparison:
    """Paired bootstrap of ``metric(model) - metric(baseline)`` over test rows.

    Each bootstrap round resamples row indices with replacement ONCE and scores
    both predictors on that same resample, so the delta distribution reflects
    model difference, not resampling luck. The one-sided p-value is the fraction
    of rounds in which the model failed to beat the baseline (with the usual
    +1/+1 continuity correction so p is never exactly 0).
    """
    n = len(y_true)
    if not (n == len(p_model) == len(p_baseline)):
        raise ValueError("y_true / p_model / p_baseline length mismatch")
    if n == 0:
        raise ValueError("cannot bootstrap an empty test set")
    sign = 1.0 if higher_is_better else -1.0
    obs_model = metric(y_true, p_model)
    obs_base = metric(y_true, p_baseline)

    rng = random.Random(seed)
    deltas: list[float] = []
    worse = 0
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        yt = [y_true[i] for i in idx]
        d = sign * (metric(yt, [p_model[i] for i in idx]) - metric(yt, [p_baseline[i] for i in idx]))
        deltas.append(d)
        if d <= 0:
            worse += 1
    deltas.sort()
    lo = deltas[max(0, int(0.025 * n_boot) - 1)]
    hi = deltas[min(n_boot - 1, int(0.975 * n_boot))]
    return PairedComparison(
        metric=metric_name,
        higher_is_better=higher_is_better,
        model_score=obs_model,
        baseline_score=obs_base,
        delta_mean=sum(deltas) / len(deltas),
        ci_low=lo,
        ci_high=hi,
        p_value=(worse + 1) / (n_boot + 1),
        n_boot=n_boot,
    )


def compare_auc(
    y_true: Sequence[Any],
    p_model: Sequence[float],
    p_baseline: Sequence[float],
    *,
    n_boot: int = 1000,
    seed: int = 0,
) -> PairedComparison:
    """Paired-bootstrap AUC comparison (higher is better)."""
    return paired_bootstrap(
        y_true, p_model, p_baseline,
        metric=roc_auc, metric_name="auc", higher_is_better=True,
        n_boot=n_boot, seed=seed,
    )


def compare_brier(
    y_true: Sequence[Any],
    p_model: Sequence[float],
    p_baseline: Sequence[float],
    *,
    n_boot: int = 1000,
    seed: int = 0,
) -> PairedComparison:
    """Paired-bootstrap Brier comparison (lower is better; delta sign-flipped)."""
    return paired_bootstrap(
        y_true, p_model, p_baseline,
        metric=brier_score, metric_name="brier", higher_is_better=False,
        n_boot=n_boot, seed=seed,
    )


def bootstrap_ci(
    y_true: Sequence[Any],
    y_prob: Sequence[float],
    *,
    metric: Callable[[Sequence[Any], Sequence[float]], float],
    n_boot: int = 1000,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Single-model percentile bootstrap: ``(observed, ci_low, ci_high)``.

    The workhorse for rare-event slices, where the point estimate alone is
    meaningless (an AUC from 12 severe events needs its interval shown).
    """
    n = len(y_true)
    if n == 0:
        raise ValueError("cannot bootstrap an empty set")
    rng = random.Random(seed)
    obs = metric(y_true, y_prob)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        vals.append(metric([y_true[i] for i in idx], [y_prob[i] for i in idx]))
    vals.sort()
    lo = vals[max(0, int(0.025 * n_boot) - 1)]
    hi = vals[min(n_boot - 1, int(0.975 * n_boot))]
    return obs, lo, hi
