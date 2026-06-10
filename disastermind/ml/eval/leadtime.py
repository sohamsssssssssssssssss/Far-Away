"""Lead-time-vs-POD — does the model warn *in time to act*, not just accurately?

A forecast that is accurate at t+0 is operationally useless: the water is already
at the door. For an evacuation system the load-bearing question is **how many
hours/days of actionable warning** the model delivers at the threshold we would
dispatch on. This module answers it by training one detector per lead time *h*
("will the hazard threshold be crossed exactly h days ahead?") and reporting, per
horizon:

  * **POD** at the operating threshold — of the events that occur h days later,
    how many do we flag h days in advance?
  * **FAR** and **AUC** for context, and the event count behind each point.

The curve POD(h) vs h is the lead-time skill curve; it must stay useful out to a
horizon that exceeds the evacuation **clearance time** (Session B's number) or the
warning cannot drive an evacuation. The module also emits the **risk trajectory**
— per location, p_event at each lead horizon — which is the agreed interface with
the evacuation/decision layer (a forecast sharpening as the event nears).

The detector is the project's deterministic stdlib logistic fit, fit on a strict
train split and scored on a strictly-later test split (no leakage), with a
balanced option for rare events. Operating thresholds are chosen on the train
split, never on test. Stdlib only, deterministic.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .decision import confusion_at, operating_point_for_pod
from .metrics import roc_auc

#: ``fit(X, y) -> predict`` factory (matches disastermind.ml.eval.crossval).
FitFactory = Callable[
    [list[list[float]], list[int]], Callable[[list[list[float]]], list[float]]
]


@dataclass(frozen=True)
class LeadPoint:
    """Skill at one lead time (one point on the POD-vs-lead-time curve)."""

    lead_hours: int
    n_test: int
    events: int
    threshold: float
    pod: float
    far: float
    auc: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead_hours": self.lead_hours,
            "n_test": self.n_test,
            "events": self.events,
            "threshold": self.threshold,
            "pod": round(self.pod, 4),
            "far": round(self.far, 4),
            "auc": round(self.auc, 4),
        }


def lead_time_curve(
    X_train: Sequence[Sequence[float]],
    horizon_labels_train: Sequence[Sequence[int]],
    X_test: Sequence[Sequence[float]],
    horizon_labels_test: Sequence[Sequence[int]],
    lead_hours: Sequence[int],
    fit: FitFactory,
    *,
    target_pod: float = 0.9,
) -> list[LeadPoint]:
    """POD/FAR/AUC at each lead time on a strictly out-of-sample test split.

    ``horizon_labels_*`` is one label row per sample, columns aligned with
    ``lead_hours`` (e.g. the per-horizon flood labels). For each horizon the
    operating threshold is chosen on the TRAIN split for ``target_pod`` and the
    confusion is evaluated AT it on test, so the curve reflects the policy we'd
    actually run. Single-class horizons (no events in test) are skipped visibly.
    """
    Xtr = [list(r) for r in X_train]
    Xte = [list(r) for r in X_test]
    out: list[LeadPoint] = []
    for col, lh in enumerate(lead_hours):
        ytr = [int(row[col]) for row in horizon_labels_train]
        yte = [int(row[col]) for row in horizon_labels_test]
        if len(set(ytr)) < 2 or sum(yte) == 0:
            continue
        predict = fit(Xtr, ytr)
        p_tr = predict(Xtr)
        p_te = predict(Xte)
        threshold = operating_point_for_pod(ytr, p_tr, target_pod).threshold
        c = confusion_at(yte, p_te, threshold)
        out.append(
            LeadPoint(
                lead_hours=lh,
                n_test=len(yte),
                events=sum(yte),
                threshold=threshold,
                pod=c.pod,
                far=c.far,
                auc=roc_auc(yte, p_te),
            )
        )
    return out


def actionable_lead_time(
    curve: Sequence[LeadPoint], *, min_pod: float = 0.8
) -> int | None:
    """Longest lead (hours) whose POD holds ``min_pod`` *continuously to impact*.

    The conservative, evacuation-safe definition (shared with the evacuation
    layer's per-forecast ``actionable_lead_hours``): walk horizons from the
    shortest lead outward and extend only while POD stays above the bar, so a
    transient long-range blip sitting above a gap is NOT counted as warning. This
    is the **validated capability** — a ceiling on what any single live forecast
    may operationally claim (the decision layer takes ``min(operational,
    validated)``). Returns ``None`` if even the shortest horizon misses the bar.
    """
    best: int | None = None
    for p in sorted(curve, key=lambda q: q.lead_hours):
        if p.pod >= min_pod:
            best = p.lead_hours
        else:
            break
    return best


def risk_trajectory(
    location_id: str,
    issued_at: str,
    features: Sequence[float],
    detectors: dict[int, Callable[[list[list[float]]], list[float]]],
    threshold: float,
    far_by_lead: dict[int, float] | None = None,
) -> dict[str, Any]:
    """The agreed interface with the evacuation layer: p_event per lead horizon.

    ``detectors`` maps lead hours -> a fitted per-horizon predict function. The
    output is one location's forecast across lead times — the shape Session B's
    clearance-time logic consumes (and the same shape its cyclone-landfall
    extrapolation already produces): a trajectory that should sharpen as the
    event nears.

    ``far_by_lead`` (optional) attaches the validated false-alarm rate at each
    lead — taken from the lead-time *curve* (``LeadPoint.far``) — so the
    evacuation layer's compliance model can apply its cry-wolf trust penalty.
    Omitted -> no ``far`` field (the consumer then applies no penalty, by
    contract). This is the producer side of the FAR contract extension.
    """
    horizons = []
    for lh in sorted(detectors):
        h: dict[str, Any] = {
            "lead_hours": lh,
            "p_event": round(detectors[lh]([list(features)])[0], 4),
        }
        if far_by_lead is not None and lh in far_by_lead:
            h["far"] = round(far_by_lead[lh], 4)
        horizons.append(h)
    return {
        "location_id": location_id,
        "issued_at": issued_at,
        "threshold": threshold,
        "horizons": horizons,
    }


def far_by_lead(curve: Sequence[LeadPoint]) -> dict[int, float]:
    """``{lead_hours: FAR}`` from a validation curve — feeds ``risk_trajectory``.

    The bridge that lets a live forecast carry the *validated* false-alarm rate
    at each lead, which the evacuation layer's compliance model consumes.
    """
    return {p.lead_hours: p.far for p in curve}


def to_dict(curve: Sequence[LeadPoint]) -> dict[str, Any]:
    """JSON-serialisable curve plus the headline actionable-lead-time summary."""
    return {
        "curve": [p.to_dict() for p in curve],
        "actionable_lead_hours_at_pod80": actionable_lead_time(curve, min_pod=0.8),
    }
