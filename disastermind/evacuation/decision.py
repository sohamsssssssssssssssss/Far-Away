"""The integration capstone — one per-zone evacuation DECISION, end to end.

Ties the lane's four pieces into a single recommendation a human commander can
act on and be accountable for:

  forecast (risk trajectory)  ->  clearance + vulnerability (phased plan: by when,
  for whom)  ->  dissemination (who actually moves, and who is left behind & why)
  ->  cost/benefit (is ordering net-positive, or below break-even)
  ->  RECOMMENDATION + a defensible decision RECORD.

The recommendation is one of:
  * NO_ACTIONABLE_WARNING  — forecast buys no lead at the dispatch threshold.
  * BELOW_BREAKEVEN_HOLD   — P(event) too low: evacuating would cost net lives
                             (the over-evacuation / false-alarm guard).
  * NOT_CLEARABLE_VERTICAL — clearance > lead: cannot empty the zone in time;
                             order NOW and activate vertical / in-place shelter.
  * ORDER_BY_DEADLINE      — feasible and net-positive: issue by T-minus-X.

The decision RECORD (PRD Step 7 / accountability) captures, deterministically,
"what we knew, when, what we recommended, and the honest caveats" — the artifact
that must hold up to both *"why did you evacuate 50,000 for nothing"* and *"why
didn't you warn us"*. (The tamper-evident audit chain itself is Session A / the
audit module's job; this is the decision-content record.)

Pure, deterministic, stdlib-only. Cyclone / flood / fire only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .dissemination import CHANNELS, assess_dissemination
from .risk_trajectory import RiskTrajectory, actionable_lead_hours
from .tradeoff import evacuation_tradeoff
from .vulnerability import DEFAULT_COHORTS, Cohort, plan_phased_evacuation

NO_ACTIONABLE_WARNING = "NO_ACTIONABLE_WARNING"
BELOW_BREAKEVEN_HOLD = "BELOW_BREAKEVEN_HOLD"
NOT_CLEARABLE_VERTICAL = "NOT_CLEARABLE_VERTICAL"
ORDER_BY_DEADLINE = "ORDER_BY_DEADLINE"


def _p_event_at_lead(traj: RiskTrajectory, lead: int) -> float:
    """Forecast probability at the actionable-lead horizon (0 if none)."""
    matches = [h.p_event for h in traj.horizons if h.lead_hours == lead]
    if matches:
        return max(matches)
    crossing = [h.p_event for h in traj.horizons if h.p_event >= traj.threshold]
    return max(crossing) if crossing else 0.0


@dataclass
class ZoneEvacuationDecision:
    zone_id: str
    issued_at: str
    actionable_lead_hours: int
    p_event: float
    far: float | None
    recommendation: str
    must_issue_by_hours: float
    expected_moved: int
    population: int
    equity_ok: bool
    stranded_cohorts: list[str]
    net_lives_saved: float
    break_even_p: float
    decision_record: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def decide_zone_evacuation(
    zone_id: str,
    population: int,
    road_egress_pph: float,
    assisted_egress_pph: float,
    traj: RiskTrajectory,
    *,
    active_channels: tuple[str, ...] = CHANNELS,
    prior_false_alarms: int = 0,
    cohorts: tuple[Cohort, ...] = DEFAULT_COHORTS,
    stay_fatality_rate: float = 0.02,
    last_mile_hours: float = 0.75,
) -> ZoneEvacuationDecision:
    """Produce the end-to-end evacuation decision + record for one zone."""
    lead = actionable_lead_hours(traj)
    p_event = _p_event_at_lead(traj, lead)

    plan = plan_phased_evacuation(
        population, road_egress_pph, assisted_egress_pph, traj, cohorts,
        last_mile_hours=last_mile_hours,
    )
    diss = assess_dissemination(plan, traj, active_channels, prior_false_alarms=prior_false_alarms)
    moved_by_cohort = {c.name: c.moved for c in diss.cohorts}
    trade = evacuation_tradeoff(
        moved_by_cohort, p_event, stay_fatality_rate=stay_fatality_rate,
        false_alarm_threshold=traj.threshold,
    )
    # worst (highest) break-even among cohorts that were actually moved
    break_even = max((c.break_even_p for c in trade.cohorts), default=0.0)

    # ---- recommendation logic ------------------------------------------------
    if lead <= 0:
        rec = NO_ACTIONABLE_WARNING
        must_by = 0.0
    elif p_event < break_even:
        rec = BELOW_BREAKEVEN_HOLD  # over-evacuation guard: would cost net lives
        must_by = 0.0
    elif not all(c.feasible for c in plan.cohorts):
        rec = NOT_CLEARABLE_VERTICAL  # cannot empty in time
        must_by = plan.first_order_deadline_hours
    else:
        rec = ORDER_BY_DEADLINE
        must_by = plan.first_order_deadline_hours

    record = _decision_record(zone_id, traj, lead, p_event, plan, diss, trade, rec, must_by)
    notes = [
        "End-to-end: forecast -> phased clearance -> dissemination -> cost/benefit. "
        "Every component's assumptions are explicit and unvalidated; this is a "
        "decision-support recommendation, not an autonomous order.",
        "The human commander owns the legal/political risk of acting; this record "
        "is the defensible 'what we knew, when, what we recommended'.",
    ]
    return ZoneEvacuationDecision(
        zone_id=zone_id, issued_at=traj.issued_at, actionable_lead_hours=lead,
        p_event=round(p_event, 3), far=diss.far, recommendation=rec,
        must_issue_by_hours=round(must_by, 2), expected_moved=diss.total_moved,
        population=population, equity_ok=plan.equity_ok, stranded_cohorts=plan.stranded_cohorts,
        net_lives_saved=trade.total_lives_saved, break_even_p=round(break_even, 4),
        decision_record=record, notes=notes,
    )


def _decision_record(
    zone_id, traj, lead, p_event, plan, diss, trade, rec, must_by
) -> dict:
    """The defensible decision-content record (deterministic; no wall-clock)."""
    return {
        "what_we_knew": {
            "zone": zone_id,
            "forecast_issued_at": traj.issued_at,
            "actionable_lead_hours": lead,
            "p_event_at_lead": round(p_event, 3),
            "false_alarm_rate": diss.far,
            "dispatch_threshold": traj.threshold,
        },
        "what_we_recommended": {
            "recommendation": rec,
            "issue_order_by_T_minus_hours": round(must_by, 2),
            "expected_evacuated": diss.total_moved,
            "of_population": plan.total_population,
        },
        "who_is_left_behind_and_why": [
            {"cohort": c.name, "not_reached": c.not_reached,
             "would_not_comply": c.noncompliant, "could_not_be_moved": c.stranded}
            for c in diss.cohorts
        ],
        "equity": {
            "ok": plan.equity_ok,
            "stranded_vulnerable_cohorts": plan.stranded_cohorts,
        },
        "cost_benefit": {
            "net_lives_saved": trade.total_lives_saved,
            "evacuation_casualties": trade.total_evac_casualties,
            "cry_wolf_increment_if_false_alarm": trade.cry_wolf_increment,
        },
        "caveats": [
            "Egress / assisted-transport capacities, compliance rates, and "
            "casualty rates are explicit, tunable, UNVALIDATED planning assumptions.",
            "Recommendation is decision-support; the human commander holds authority "
            "and accountability for any order.",
        ],
    }


def to_markdown(d: ZoneEvacuationDecision) -> str:
    headline = {
        NO_ACTIONABLE_WARNING: "⏸  No actionable warning yet — monitor.",
        BELOW_BREAKEVEN_HOLD: "⏸  HOLD — P(event) below break-even; evacuating would "
                              "cost net lives (false-alarm guard).",
        NOT_CLEARABLE_VERTICAL: "🔴 ORDER NOW + VERTICAL EVACUATION — zone cannot be "
                                "emptied in the available lead.",
        ORDER_BY_DEADLINE: f"🟢 ISSUE EVACUATION ORDER by T-minus-{d.must_issue_by_hours:.1f} h.",
    }[d.recommendation]
    return "\n".join([
        f"# Zone Evacuation Decision — {d.zone_id}",
        "",
        f"**{headline}**",
        "",
        f"- Forecast issued {d.issued_at}: actionable lead **{d.actionable_lead_hours} h**, "
        f"P(event) {d.p_event:.0%}, FAR {('%.0f%%' % (100 * d.far)) if d.far is not None else 'n/a'}",
        f"- Expected evacuated: **{d.expected_moved:,} / {d.population:,}** "
        f"({100 * d.expected_moved / d.population:.0f}%)",
        f"- Equity: {'✅ all vulnerable cohorts clearable' if d.equity_ok else '❌ stranded: ' + ', '.join(d.stranded_cohorts)}",
        f"- Cost/benefit: net lives saved {d.net_lives_saved:+.0f}; "
        f"evacuation break-even at P(event) ≥ {d.break_even_p:.1%}",
        "",
        "## Decision record (accountability)",
        "_'What we knew, when, what we recommended' — the defensible artifact._",
        "",
        "## Honest limits",
        *[f"- {n}" for n in d.notes],
    ])
