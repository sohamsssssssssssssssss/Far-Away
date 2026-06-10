"""Fairness audit — does the model systematically under-protect anyone?

The PRD's equity premise makes this a validation REQUIREMENT, not a nicety: a
model that is accurate on average but blind to rural floodplain villages (or to
moderate quakes in poorly-instrumented regions) fails the mission even with a
beautiful AUC. This module measures, per declared subgroup,

  * **POD at the shared operating threshold** — the life-safety number. The
    threshold is GLOBAL (one dispatch policy), so a subgroup whose events score
    systematically lower than others' shows up as a POD gap here.
  * FAR, AUC, base rate and n — context for interpreting the gap.

and flags any subgroup whose POD falls more than ``tolerance`` below the overall
POD (with an ``n`` floor so tiny groups don't produce noise-flags). The output is
a publishable audit table: groups, numbers, flags — no averaging anything away.

Group keys are caller-supplied strings (e.g. ``"setting:rural"``,
``"region:northeast"``, ``"mag:6-7"``), so each hazard dataset decides its own
equity axes and the audit machinery stays generic. Stdlib only, deterministic.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .decision import confusion_at
from .metrics import roc_auc


@dataclass(frozen=True)
class GroupReport:
    """Audit row for one subgroup at the shared operating threshold."""

    group: str
    n: int
    positives: int
    pod: float | None  # None when the group has no positive events to detect
    far: float
    auc: float | None  # None when single-class (undefined)
    base_rate: float
    under_protected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "n": self.n,
            "positives": self.positives,
            "pod": self.pod,
            "far": self.far,
            "auc": self.auc,
            "base_rate": self.base_rate,
            "under_protected": self.under_protected,
        }


def audit_subgroups(
    y_true: Sequence[int],
    y_prob: Sequence[float],
    groups: Sequence[str],
    *,
    threshold: float,
    tolerance: float = 0.05,
    min_n: int = 30,
    group_thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Per-subgroup performance, with gap flags.

    By default every group is scored at the SHARED ``threshold`` — this is what
    *reveals* under-protection (a group whose events score systematically lower
    gets fewer detections at one global cutoff). Pass ``group_thresholds`` to
    instead score each group at its own operating point (the equalized-odds
    remediation; see :func:`equalized_thresholds`) — the gap closes by
    construction, and the per-group FAR rises, which the report carries so the
    cost of equity is explicit, never hidden.

    A group is flagged ``under_protected`` when it has at least ``min_n`` rows,
    at least one real event, and its POD is more than ``tolerance`` below the
    overall POD. The audit is published in full, including the uncomfortable rows.
    """
    if not (len(y_true) == len(y_prob) == len(groups)):
        raise ValueError("y_true / y_prob / groups length mismatch")
    overall = confusion_at(y_true, y_prob, threshold)
    overall_pod = overall.pod

    rows: list[GroupReport] = []
    for g in sorted(set(groups)):
        idx = [i for i, gg in enumerate(groups) if gg == g]
        yt = [y_true[i] for i in idx]
        yp = [y_prob[i] for i in idx]
        g_threshold = (group_thresholds or {}).get(g, threshold)
        c = confusion_at(yt, yp, g_threshold)
        n_pos = sum(1 for v in yt if v)
        pod = c.pod if n_pos else None
        flagged = (
            len(idx) >= min_n
            and n_pos > 0
            and pod is not None
            and pod < overall_pod - tolerance
        )
        rows.append(
            GroupReport(
                group=g,
                n=len(idx),
                positives=n_pos,
                pod=pod,
                far=c.far,
                auc=roc_auc(yt, yp) if 0 < n_pos < len(idx) else None,
                base_rate=n_pos / len(idx) if idx else 0.0,
                under_protected=flagged,
            )
        )

    flagged = [r.group for r in rows if r.under_protected]
    return {
        "threshold": threshold,
        "group_thresholds": dict(group_thresholds) if group_thresholds else None,
        "tolerance": tolerance,
        "min_n": min_n,
        "overall": {"n": overall.n, "pod": overall_pod, "far": overall.far},
        "groups": [r.to_dict() for r in rows],
        "under_protected_groups": flagged,
        "passed": not flagged,
    }


def equalized_thresholds(
    y_true: Sequence[int],
    y_prob: Sequence[float],
    groups: Sequence[str],
    *,
    target_pod: float,
    fallback: float,
    min_n: int = 30,
) -> dict[str, float]:
    """Per-group operating thresholds that bring each group to ``target_pod``.

    The standard equalized-odds remediation for the under-protection a single
    global threshold creates: each group gets the highest threshold at which its
    own POD still meets ``target_pod`` (computed on the data passed — use a
    CALIBRATION split, never test). Groups too small or single-class keep the
    ``fallback`` (the global threshold). For flood this mirrors real practice —
    every river gauge already has its own warning threshold.
    """
    from .decision import operating_point_for_pod

    out: dict[str, float] = {}
    for g in sorted(set(groups)):
        idx = [i for i, gg in enumerate(groups) if gg == g]
        yt = [y_true[i] for i in idx]
        yp = [y_prob[i] for i in idx]
        if len(idx) < min_n or not (0 < sum(yt) < len(yt)):
            out[g] = fallback
            continue
        out[g] = operating_point_for_pod(yt, yp, target_pod).threshold
    return out


def remediate(
    y_true: Sequence[int],
    y_prob: Sequence[float],
    groups: Sequence[str],
    *,
    threshold: float,
    target_pod: float,
    group_thresholds: dict[str, float],
    tolerance: float = 0.05,
    min_n: int = 30,
) -> dict[str, Any]:
    """Before/after the equalized-odds remediation, with the FAR cost of equity.

    ``threshold`` is the shared global cutoff (the ``before``); ``group_thresholds``
    are the per-group operating points fitted on the calibration split (apply the
    ``after`` to the TEST split here). Returns both audits plus the extra-false-
    alarm cost the remediation incurs — equity is not free, and the price is
    stated.
    """
    before = audit_subgroups(
        y_true, y_prob, groups, threshold=threshold, tolerance=tolerance, min_n=min_n
    )
    after = audit_subgroups(
        y_true, y_prob, groups, threshold=threshold, tolerance=tolerance,
        min_n=min_n, group_thresholds=group_thresholds,
    )
    far_before = {r["group"]: r["far"] for r in before["groups"]}
    far_after = {r["group"]: r["far"] for r in after["groups"]}
    auc_by_group = {r["group"]: r["auc"] for r in after["groups"]}
    far_cost = {
        g: round(far_after[g] - far_before[g], 4)
        for g in far_after
        if g in before["under_protected_groups"]
    }
    # Classify each residual (still-flagged after remediation) by CAUSE: a group
    # whose AUC is weak can't be fixed by a threshold (it's a discrimination
    # deficit -> needs better inputs/features), vs one that just needed its own
    # operating point. This makes the residual an actionable finding, not a fail.
    residual_cause = {}
    for g in after["under_protected_groups"]:
        auc = auc_by_group.get(g)
        residual_cause[g] = (
            "discrimination deficit — needs better inputs/features (threshold cannot fix)"
            if (auc is not None and auc < 0.85)
            else "residual threshold gap (consider a lower group threshold / more data)"
        )
    return {
        "target_pod": target_pod,
        "before": {
            "under_protected_groups": before["under_protected_groups"],
            "passed": before["passed"],
        },
        "after": {
            "under_protected_groups": after["under_protected_groups"],
            "passed": after["passed"],
            "group_thresholds": group_thresholds,
        },
        "far_cost_of_equity": far_cost,
        "residual_cause": residual_cause,
        "audit_after": after,
    }
