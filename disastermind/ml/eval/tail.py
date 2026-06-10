"""Rare-severe-event evaluation — skill on the events that actually matter.

Average metrics are dominated by the common small events; the M7+ quake and the
record monsoon discharge are exactly where most models quietly fail and where
failure costs the most. This module evaluates the model ON SEVERITY SLICES of
the held-out test set, with bootstrap confidence intervals (tail slices are
small, so a point estimate without an interval would be a lie of precision):

  * **detection within the tail** — POD over the severe events alone at the
    operational threshold: of the worst events, how many would we have caught?
  * **discrimination into the tail** — AUC of the model separating severe
    events from everything else: does a higher score actually mean "worse"?
  * per-slice Brier and the slice sizes, so reviewers see exactly how much
    evidence the tail claim rests on.

Slices are caller-defined predicates (e.g. magnitude bands, discharge return
periods, fire-size classes) so every hazard publishes the same shaped table.
Stdlib only, deterministic via seeded bootstrap.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .decision import confusion_at
from .metrics import brier_score, roc_auc
from .significance import bootstrap_ci


@dataclass(frozen=True)
class SeveritySlice:
    """One named severity stratum, selected by a per-row predicate.

    ``member`` receives the caller's per-row severity payload (a magnitude, a
    discharge percentile, a fire size...) and returns membership. Slices may
    overlap (e.g. "M6+" contains "M7+") — that is intended; each row of the
    published table stands alone.
    """

    name: str
    member: Callable[[Any], bool]


def tail_report(
    y_true: Sequence[int],
    y_prob: Sequence[float],
    severity: Sequence[Any],
    slices: Sequence[SeveritySlice],
    *,
    threshold: float,
    n_boot: int = 500,
    seed: int = 0,
) -> dict[str, Any]:
    """Severity-stratified scorecard with bootstrap CIs.

    For each slice: POD (+CI) at the operational ``threshold`` over the slice's
    real events, AUC (+CI) of severe-vs-rest discrimination over the WHOLE test
    set (label = slice membership AND event), Brier within the slice, and counts.
    Slices with no events are reported with null metrics rather than dropped, so
    "we had no M8 to test on" is visible instead of silently absent.
    """
    if not (len(y_true) == len(y_prob) == len(severity)):
        raise ValueError("y_true / y_prob / severity length mismatch")
    out: list[dict[str, Any]] = []
    for s in slices:
        in_slice = [bool(s.member(v)) for v in severity]
        idx = [i for i, m in enumerate(in_slice) if m and y_true[i]]
        row: dict[str, Any] = {"slice": s.name, "events": len(idx)}

        if idx:
            # POD over the slice's real events, with CI from a bootstrap of the
            # events themselves (binomial-style resampling).
            yt = [1] * len(idx)
            yp = [y_prob[i] for i in idx]
            pod, lo, hi = bootstrap_ci(
                yt,
                yp,
                metric=lambda a, b: confusion_at(a, b, threshold).pod,
                n_boot=n_boot,
                seed=seed,
            )
            row["pod"] = pod
            row["pod_ci95"] = [lo, hi]
            row["brier"] = brier_score(yt, yp)
        else:
            row["pod"] = None
            row["pod_ci95"] = None
            row["brier"] = None

        # Severe-vs-rest discrimination across the full test set.
        sv = [1 if (m and lab) else 0 for m, lab in zip(in_slice, y_true)]
        if 0 < sum(sv) < len(sv):
            auc, alo, ahi = bootstrap_ci(
                sv, list(y_prob), metric=roc_auc, n_boot=n_boot, seed=seed
            )
            row["auc_severe_vs_rest"] = auc
            row["auc_ci95"] = [alo, ahi]
        else:
            row["auc_severe_vs_rest"] = None
            row["auc_ci95"] = None
        out.append(row)

    return {"threshold": threshold, "n_boot": n_boot, "slices": out}
