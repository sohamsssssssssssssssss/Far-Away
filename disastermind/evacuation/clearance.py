"""Clearance-time decision — the core evacuation question.

Given a zone's population, road egress capacity and last-mile travel, how long to
EMPTY the zone (clearance time)? Then, against a forecast's actionable lead time
(the risk-trajectory contract), decide **by when the order must be issued** — or
flag that the zone is *not clearable in time*.

Clearance time (a standard evacuation-planning decomposition):

    T_clear = T_mobilize + T_egress + T_lastmile
            = mobilization lag
            + (population * participation) / egress_capacity      # the queue term
            + travel time of the last evacuee to shelter

HONESTY (every assumption is explicit and tunable, none hidden):
  * ``egress_capacity_pph`` (persons/hour the network can move out) is the
    DOMINANT uncertainty. We do NOT pretend to derive it precisely from OSM —
    :func:`egress_capacity_from_roads` is a rough reference only, and the report
    leads with a SENSITIVITY across a plausible range, not one false-precise
    number. A real plan substitutes surveyed evacuation-route capacities.
  * ``mobilization_hours`` and ``participation`` are planning assumptions
    (warning-to-movement lag; the fraction who actually move). Compliance is
    modelled elsewhere; here participation is an input, not a measured rate.
  * Coastal-India context: evacuation is largely SHORT-RANGE to local cyclone
    shelters (the OSDMA model), not US-style highway car-egress — so the
    last-mile term is small and the queue/intake term dominates.

Pure, deterministic, stdlib-only. No wall-clock, no global RNG.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .risk_trajectory import RiskTrajectory, actionable_lead_hours

# Documented per-class egress capacity (persons/hour) for a single major road of
# that class under evacuation conditions (mixed bus/cycle/foot/car). Planning
# figures, deliberately conservative; override with surveyed values.
DEFAULT_PER_CLASS_PPH: dict[str, float] = {
    "trunk": 2400.0,
    "primary": 1800.0,
    "secondary": 1200.0,
}
#: Major OSM way-count over-states distinct corridors badly; scale it down to an
#: order-of-magnitude corridor proxy. This is a ROUGH reference only (see report).
WAY_TO_CORRIDOR = 0.05


@dataclass
class ClearanceEstimate:
    population: int
    participation: float
    egress_capacity_pph: float
    mobilization_hours: float
    last_mile_hours: float
    egress_hours: float
    clearance_hours: float
    breakdown: dict = field(default_factory=dict)


def egress_capacity_from_roads(
    road_counts: dict[str, int], per_class_pph: dict[str, float] | None = None
) -> float:
    """ROUGH reference egress capacity (persons/hour) from real major-road counts.

    Heavy caveat: OSM way-counts over-state distinct evacuation corridors, so this
    is an order-of-magnitude estimate (scaled by :data:`WAY_TO_CORRIDOR`), NOT a
    survey. The report uses it as ONE point on a sensitivity, never as truth.
    """
    per = per_class_pph or DEFAULT_PER_CLASS_PPH
    total = 0.0
    for cls, pph in per.items():
        total += road_counts.get(cls, 0) * WAY_TO_CORRIDOR * pph
    return round(total, 1)


#: Contraflow (reversing inbound lanes for outbound evacuation) does NOT double
#: egress: ramp/merge/termination losses cap the real gain at ~1.7-1.8x (a
#: well-documented evacuation-engineering figure). Tunable.
DEFAULT_CONTRAFLOW_FACTOR = 1.8


def contraflow_egress(base_egress_pph: float, factor: float = DEFAULT_CONTRAFLOW_FACTOR) -> float:
    """Egress capacity with contraflow: reversing inbound lanes raises outbound
    throughput by ``factor`` (~1.8x, NOT 2x — merge/termination losses)."""
    return round(base_egress_pph * factor, 1)


def estimate_clearance(
    population: int,
    egress_capacity_pph: float,
    *,
    participation: float = 0.9,
    mobilization_hours: float = 4.0,
    last_mile_hours: float = 0.75,
) -> ClearanceEstimate:
    """Estimate hours to clear (empty) the zone. All terms explicit."""
    demand = population * participation
    egress_hours = demand / egress_capacity_pph if egress_capacity_pph > 0 else float("inf")
    clearance = mobilization_hours + egress_hours + last_mile_hours
    return ClearanceEstimate(
        population=population,
        participation=participation,
        egress_capacity_pph=egress_capacity_pph,
        mobilization_hours=mobilization_hours,
        last_mile_hours=last_mile_hours,
        egress_hours=round(egress_hours, 2),
        clearance_hours=round(clearance, 2),
        breakdown={
            "mobilization": mobilization_hours,
            "egress_queue": round(egress_hours, 2),
            "last_mile": last_mile_hours,
        },
    )


@dataclass
class EvacuationDecision:
    location_id: str
    actionable_lead_hours: int
    clearance_hours: float
    feasible: bool
    #: hours-before-impact by which the order MUST be issued (T-minus value).
    must_issue_by_hours: float
    #: spare hours when feasible; hours of un-clearable shortfall when not.
    slack_hours: float
    message: str


def decide(estimate: ClearanceEstimate, traj: RiskTrajectory) -> EvacuationDecision:
    """Compare clearance time against the forecast's actionable lead time.

    Impact at ``I``. Clearing takes ``C`` hours, so the zone is empty by ``I``
    only if the order is issued by ``I - C`` ("T-minus-C"). The forecast buys
    ``H`` hours of warning (the longest lead clearing the dispatch threshold).
      * ``H >= C`` -> feasible; must issue by T-minus-C; slack ``H - C``.
      * ``H <  C`` -> NOT clearable: even ordering the instant you know, the zone
        empties ``C - H`` hours AFTER impact. The critical, honest flag.
    """
    H = actionable_lead_hours(traj)
    C = estimate.clearance_hours
    feasible = H >= C
    if feasible:
        msg = (
            f"Feasible: issue the order by T-minus-{C:.1f} h; "
            f"forecast buys {H} h of warning -> {H - C:.1f} h slack."
        )
        slack = round(H - C, 2)
    else:
        msg = (
            f"NOT CLEARABLE in time: clearance {C:.1f} h > {H} h of warning. "
            f"Even ordering immediately, the zone empties ~{C - H:.1f} h after "
            "impact. Mitigations: earlier trigger (lower threshold / longer-lead "
            "model), vertical evacuation / in-place shelters, or more egress capacity."
        )
        slack = round(H - C, 2)  # negative => shortfall
    return EvacuationDecision(
        location_id=traj.location_id,
        actionable_lead_hours=H,
        clearance_hours=C,
        feasible=feasible,
        must_issue_by_hours=round(C, 2),
        slack_hours=slack,
        message=msg,
    )


def clearance_sensitivity(
    population: int,
    egress_range_pph: list[float],
    traj: RiskTrajectory,
    **kw,
) -> list[tuple[float, float, bool]]:
    """(egress_pph, clearance_hours, feasible) across a plausible egress range.

    The honest answer to the dominant uncertainty: show how the decision flips
    with egress capacity, rather than asserting one number.
    """
    out = []
    for e in egress_range_pph:
        est = estimate_clearance(population, e, **kw)
        d = decide(est, traj)
        out.append((e, est.clearance_hours, d.feasible))
    return out


def to_markdown(
    estimate: ClearanceEstimate, decision: EvacuationDecision, sensitivity: list | None = None
) -> str:
    lines = [
        "# Evacuation Clearance-Time Decision",
        "",
        f"_Zone {decision.location_id}: population {estimate.population:,}, "
        f"participation {estimate.participation:.0%}._",
        "",
        "## Clearance time (hours to empty the zone)",
        f"- mobilization {estimate.mobilization_hours} h + egress-queue "
        f"{estimate.egress_hours} h + last-mile {estimate.last_mile_hours} h "
        f"= **{estimate.clearance_hours} h** (at {estimate.egress_capacity_pph:,.0f} persons/h egress)",
        "",
        "## Decision vs the forecast's warning",
        f"- Forecast actionable lead: **{decision.actionable_lead_hours} h**",
        f"- **{'FEASIBLE' if decision.feasible else 'NOT CLEARABLE'}** — {decision.message}",
    ]
    if sensitivity:
        lines += [
            "",
            "## Sensitivity to egress capacity (the dominant uncertainty)",
            "| Egress (persons/h) | Clearance (h) | Feasible vs lead |",
            "|---|---|---|",
        ]
        for e, c, ok in sensitivity:
            lines.append(f"| {e:,.0f} | {c:.1f} | {'✅' if ok else '❌'} |")
    lines += [
        "",
        "## Honest limits",
        "- Egress capacity is the dominant uncertainty and is NOT precisely derivable "
        "from OSM; the sensitivity above is the real answer. A deployment needs "
        "surveyed evacuation-route capacities.",
        "- Mobilization lag and participation are planning assumptions; compliance "
        "(whether people actually leave) is modelled separately, not assumed here.",
        "- Applies to cyclone/flood/fire only; earthquakes are impact-triage, not "
        "evacuation forecasting.",
    ]
    return "\n".join(lines)
