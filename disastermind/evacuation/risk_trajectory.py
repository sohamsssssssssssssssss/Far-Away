"""The risk-trajectory contract — the one interface between the validation lane
(Session A) and the evacuation lane (Session B).

Agreed schema (consumed as a documented contract; NOT imported across the lane
boundary — reconciled to one definition at merge):

    {
      "location_id": str,
      "issued_at":   str,                       # ISO-8601, supplied (no wall-clock)
      "horizons":    [{"lead_hours": int, "p_event": float}, ...],
      "threshold":   float                      # the dispatch probability threshold
    }

A *risk trajectory* is a single forecast issued at ``issued_at`` that gives the
probability of the event at several lead times (e.g. t+72/48/24/12/6 h). The
**actionable lead time** is how much warning the model actually buys: the longest
lead at which the probability already clears the dispatch threshold.

The cyclone hindcast (``disastermind.hindcast``) emits the same shape — landfall
risk sharpening as landfall nears — so the two merge cleanly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Horizon:
    lead_hours: int
    p_event: float
    #: False-alarm rate at this lead (optional; PROPOSED contract extension —
    #: Session A's curve carries FAR-vs-lead). The dissemination model consumes it
    #: because trust/compliance falls as FAR rises. Additive + back-compatible:
    #: ``None`` when the producer hasn't supplied it. Reconciled at merge.
    far: float | None = None


@dataclass
class RiskTrajectory:
    location_id: str
    issued_at: str
    horizons: list[Horizon]
    threshold: float

    @classmethod
    def from_dict(cls, d: dict) -> RiskTrajectory:
        return cls(
            location_id=str(d["location_id"]),
            issued_at=str(d["issued_at"]),
            horizons=[
                Horizon(int(h["lead_hours"]), float(h["p_event"]),
                        far=(float(h["far"]) if h.get("far") is not None else None))
                for h in d["horizons"]
            ],
            threshold=float(d["threshold"]),
        )

    def to_dict(self) -> dict:
        def _h(h: Horizon) -> dict:
            out: dict = {"lead_hours": h.lead_hours, "p_event": h.p_event}
            if h.far is not None:
                out["far"] = h.far
            return out

        return {
            "location_id": self.location_id,
            "issued_at": self.issued_at,
            "horizons": [_h(h) for h in self.horizons],
            "threshold": self.threshold,
        }


def far_at_lead(traj: RiskTrajectory, lead_hours: int) -> float | None:
    """The false-alarm rate at the horizon nearest ``lead_hours`` (None if absent)."""
    with_far = [h for h in traj.horizons if h.far is not None]
    if not with_far:
        return None
    nearest = min(with_far, key=lambda h: abs(h.lead_hours - lead_hours))
    return nearest.far


def actionable_lead_hours(traj: RiskTrajectory) -> int:
    """Warning the forecast actually buys: the LONGEST lead whose probability
    already clears the dispatch threshold.

    A model accurate only at t+0 returns 0 — useless for evacuation. Returns 0
    when no horizon clears the threshold (no actionable warning).
    """
    crossing = [h.lead_hours for h in traj.horizons if h.p_event >= traj.threshold]
    return max(crossing) if crossing else 0
