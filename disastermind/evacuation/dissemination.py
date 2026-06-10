"""Dissemination + the warning-response gap — delivery is NOT compliance.

The best forecast saves no one if it doesn't reach a person, on a channel they
have, in time they trust enough to act. This models the gap between a warning
*sent* and a person *gone*, with two findings the critique asked for:

  1. Compliance consumes BOTH lead time AND the false-alarm rate at that lead.
     More lead → more time to act (compliance up); but FAR climbs steeply at long
     lead, and people don't act on alarms they don't trust (cry-wolf). The two
     pull opposite ways, so there is a **compliance-optimal lead**, not "longer is
     always better". This reads Session A's FAR-vs-lead curve directly.

  2. Compliance is per-cohort, which SHARPENS the equity finding into double
     jeopardy: the no-vehicle cohort that is physically stranded at short lead is
     also the cohort with the lowest reach on standard channels (no smartphone for
     cell-broadcast, language barriers). The same people are missed AND can't move.

"Left behind" is split by REASON — not-reached / reached-but-noncompliant /
willing-but-stranded — so a planner knows whether to add wardens (reach), improve
messaging/trust (compliance), or add buses (capacity).

*** HONESTY — the research-toy guardrail ***
Compliance cannot be validated here without real evacuation-response data. So:
every parameter is EXPLICIT, TUNABLE, and labelled UNVALIDATED; the functional
SHAPES come from well-documented qualitative findings (lead↑→compliance↑;
false-alarms→fatigue; channel access tracks income/age/language), but the
MAGNITUDES are planning assumptions, not measurements. The report leads with
sensitivity, never a single confident number.

Pure, deterministic, stdlib-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import prod

from .risk_trajectory import RiskTrajectory
from .vulnerability import PhasedEvacuation

#: Warning channels. SACHET/CAP cell-broadcast, SMS, siren, radio, door-to-door.
CHANNELS = ("cell_broadcast", "sms", "siren", "radio", "community_warden")

# Per-cohort channel REACH (probability the message lands), grounded in the
# documented access gap (smartphone/registration tracks income/age; sirens limited
# by hearing/range; wardens reach the unconnected). MAGNITUDES ARE ASSUMPTIONS.
DEFAULT_REACH: dict[str, dict[str, float]] = {
    "hospitalised/medical": {"cell_broadcast": 0.9, "sms": 0.9, "siren": 0.7, "radio": 0.7, "community_warden": 0.95},
    "elderly/mobility-impaired": {"cell_broadcast": 0.4, "sms": 0.3, "siren": 0.5, "radio": 0.6, "community_warden": 0.9},
    "transport-dependent (no motor vehicle)": {"cell_broadcast": 0.5, "sms": 0.4, "siren": 0.7, "radio": 0.6, "community_warden": 0.85},
    "children-dependent households": {"cell_broadcast": 0.8, "sms": 0.8, "siren": 0.7, "radio": 0.7, "community_warden": 0.8},
    "general (self-evacuating)": {"cell_broadcast": 0.9, "sms": 0.85, "siren": 0.7, "radio": 0.7, "community_warden": 0.7},
}

# Compliance shape parameters (ASSUMPTIONS — tune per locale).
TIME_HALF_HOURS = 18.0   # lead at which the time-to-act factor reaches 0.5
FAR_WEIGHT = 0.6         # how strongly false-alarm rate erodes trust
FATIGUE_DECAY = 0.85     # multiplicative trust loss per prior false alarm (cry-wolf)


def combined_reach(cohort: str, active_channels: tuple[str, ...], reach=DEFAULT_REACH) -> float:
    """P(message lands via >=1 active channel) = 1 - prod(1 - reach_c)."""
    r = reach.get(cohort, {})
    return 1.0 - prod(1.0 - r.get(ch, 0.0) for ch in active_channels)


def time_factor(lead_hours: float) -> float:
    """Time-to-act factor: rises with lead (saturating). 0 at no warning."""
    if lead_hours <= 0:
        return 0.0
    return lead_hours / (lead_hours + TIME_HALF_HOURS)


def trust_factor(far: float | None, prior_false_alarms: int = 0) -> float:
    """Trust factor: falls as FAR rises and with prior false alarms (cry-wolf).

    ``far is None`` (producer hasn't supplied FAR yet) -> no FAR penalty (1.0),
    flagged by the caller; do not silently assume reliability.
    """
    base = 1.0 if far is None else max(0.0, 1.0 - FAR_WEIGHT * far)
    return base * (FATIGUE_DECAY ** max(0, prior_false_alarms))


def compliance_given_reached(lead_hours: float, far: float | None, prior_false_alarms: int = 0) -> float:
    """Fraction who ACT, given the message reached them — time × trust."""
    return round(time_factor(lead_hours) * trust_factor(far, prior_false_alarms), 4)


@dataclass
class ComplianceOptimum:
    optimal_lead_hours: int
    optimal_compliance: float
    curve: list[tuple[int, float | None, float]]  # (lead, far, compliance_given_reached)
    far_supplied: bool


def compliance_curve(traj: RiskTrajectory, prior_false_alarms: int = 0) -> ComplianceOptimum:
    """Compliance-given-reached across the forecast's horizons, with the peak.

    The honest answer to 'is longer lead better?': it shows the trade between
    time-to-act and forecast trust, and the lead that maximises compliance.
    """
    curve = [
        (h.lead_hours, h.far, compliance_given_reached(h.lead_hours, h.far, prior_false_alarms))
        for h in sorted(traj.horizons, key=lambda x: x.lead_hours)
    ]
    best = max(curve, key=lambda t: t[2]) if curve else (0, None, 0.0)
    return ComplianceOptimum(
        optimal_lead_hours=best[0],
        optimal_compliance=best[2],
        curve=curve,
        far_supplied=any(h.far is not None for h in traj.horizons),
    )


@dataclass
class CohortOutcome:
    name: str
    population: int
    reach: float
    compliance: float
    moved: int
    not_reached: int
    noncompliant: int
    stranded: int  # willing but couldn't be physically moved in the lead window


@dataclass
class DisseminationAssessment:
    location_id: str
    lead_hours: int
    far: float | None
    active_channels: tuple[str, ...]
    cohorts: list[CohortOutcome]
    total_population: int
    total_moved: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["cohorts"] = [c.__dict__ for c in self.cohorts]
        d["active_channels"] = list(self.active_channels)
        return d


def assess_dissemination(
    plan: PhasedEvacuation,
    traj: RiskTrajectory,
    active_channels: tuple[str, ...] = CHANNELS,
    *,
    prior_false_alarms: int = 0,
    reach=DEFAULT_REACH,
) -> DisseminationAssessment:
    """Combine reach + compliance with the physical clearance plan -> who actually
    moves, and who is left behind and WHY (per cohort)."""
    lead = plan.actionable_lead_hours
    # FAR at the actionable lead (nearest horizon), if supplied.
    from .risk_trajectory import far_at_lead

    far = far_at_lead(traj, lead)
    comply = compliance_given_reached(lead, far, prior_false_alarms)

    outcomes: list[CohortOutcome] = []
    total_moved = 0
    for c in plan.cohorts:
        pop = c.population
        reach_p = combined_reach(c.name, active_channels, reach)
        reached = pop * reach_p
        willing = reached * comply
        # physical headroom: how much of the cohort can be moved within the lead
        # window. Transparent proxy: min(1, lead / clearance) (a stranded cohort
        # whose clearance is 2x the lead can move ~half). Labelled approximate.
        movable_frac = 1.0 if c.feasible else max(0.0, min(1.0, lead / c.clearance_hours))
        moved = min(willing, pop * movable_frac)
        not_reached = pop * (1.0 - reach_p)
        noncompliant = reached * (1.0 - comply)
        stranded = max(0.0, willing - moved)
        total_moved += int(round(moved))
        outcomes.append(CohortOutcome(
            name=c.name, population=pop, reach=round(reach_p, 3), compliance=round(comply, 3),
            moved=int(round(moved)), not_reached=int(round(not_reached)),
            noncompliant=int(round(noncompliant)), stranded=int(round(stranded)),
        ))

    notes = [
        "Compliance reads (lead, FAR-at-lead): time-to-act rises with lead, trust "
        "falls with FAR — so the compliance-optimal lead is NOT the longest. "
        + ("FAR was supplied by the forecast." if far is not None
           else "FAR NOT supplied yet -> no trust penalty applied (flagged)."),
        "Per-cohort reach exposes double jeopardy: the no-vehicle cohort is both "
        "physically stranded at short lead AND least reachable on phone channels — "
        "the same people missed on every axis.",
        "ALL magnitudes (reach, time/trust shape, movable proxy) are UNVALIDATED "
        "planning assumptions; the value is the reason-breakdown of who is left "
        "behind (reach vs trust vs capacity), not the absolute counts.",
    ]
    return DisseminationAssessment(
        location_id=plan.location_id, lead_hours=lead, far=far,
        active_channels=active_channels, cohorts=outcomes,
        total_population=plan.total_population, total_moved=total_moved, notes=notes,
    )


def to_markdown(a: DisseminationAssessment, opt: ComplianceOptimum | None = None) -> str:
    lines = [
        "# Dissemination & the Warning-Response Gap",
        "",
        f"_Zone {a.location_id}: lead {a.lead_hours} h, "
        f"FAR {('%.0f%%' % (100 * a.far)) if a.far is not None else 'n/a'}, "
        f"channels {', '.join(a.active_channels)}._",
    ]
    if opt is not None:
        lines += [
            "",
            "## Compliance-optimal lead (time-to-act vs forecast trust)",
            "| Lead (h) | FAR | Compliance if reached |",
            "|---|---|---|",
        ]
        for lead, far, comp in opt.curve:
            far_s = f"{100 * far:.0f}%" if far is not None else "—"
            star = "  ⭐" if lead == opt.optimal_lead_hours else ""
            lines.append(f"| {lead} | {far_s} | {comp:.2f}{star} |")
        lines.append(
            f"\n- **Peak compliance at ~{opt.optimal_lead_hours} h** — longer lead "
            "buys time but high FAR erodes trust; the sweet spot is in between."
        )
    lines += [
        "",
        "## Who actually moves, and who is left behind (and why)",
        "| Cohort | People | Reach | Comply | Moved | Not reached | Won't comply | Stranded |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in a.cohorts:
        lines.append(
            f"| {c.name} | {c.population:,} | {c.reach:.0%} | {c.compliance:.0%} | "
            f"{c.moved:,} | {c.not_reached:,} | {c.noncompliant:,} | {c.stranded:,} |"
        )
    lines += [
        "",
        f"- **Total moved:** {a.total_moved:,} / {a.total_population:,} "
        f"({100 * a.total_moved / a.total_population:.0f}%)",
        "",
        "## Honest limits",
        *[f"- {n}" for n in a.notes],
    ]
    return "\n".join(lines)
