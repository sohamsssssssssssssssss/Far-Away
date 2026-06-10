"""Full-pipeline historical backtest — the whole chain, scored against reality.

Shadow mode on the *past*: the cheapest honest proxy for the institutional gate.
For each real cyclone we replay the **entire** DisasterMind chain at a forecast
cutoff and check the end-to-end *decision* against what actually happened:

    real best-track @ cutoff (leak-free)
      -> risk trajectory  [reliability = Session A's VALIDATED FAR/lead curve]
      -> Session B's decide_zone_evacuation  (clearance + vulnerability +
         dissemination + cost/benefit)
      -> score vs the DOCUMENTED outcome (lead used in reality, people evacuated,
         deaths, equity).

Two honesty rules make this defensible rather than a victory lap:

  * **Reliability is real, the probability path is a labelled proxy.** The
    false-alarm rate at each lead comes from Session A's *validated* flood
    lead-time curve (``ml.eval.leadtime`` on real GloFAS data) — that part is
    measured. The per-storm ``p_event`` trajectory (rising as landfall nears) is
    a transparent monotone proxy standing in for IMD's dynamical forecast, EXACTLY
    as ``hindcast.replay`` already treats its naive landfall extrapolation. It is
    never claimed to be a skillful storm model.
  * **Scored against documented reality.** Fani (2019): ~1.2-1.5 M evacuated, 64
    deaths despite an Extremely Severe landfall — a real success. Amphan (2020):
    ~4.9 M evacuated. The backtest asks whether the system, on validated
    reliability, would have recommended an order with at least the lead reality
    used and surfaced the equity gap — and says where it would have diverged.

Pure/deterministic/stdlib + offline: the only "data" are committed fixtures and
the validated curve computed from committed fixtures. No wall-clock, no network.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..evacuation.decision import (
    NOT_CLEARABLE_VERTICAL,
    ORDER_BY_DEADLINE,
    decide_zone_evacuation,
)
from ..evacuation.risk_trajectory import Horizon, RiskTrajectory
from ..ml.eval.leadtime import far_by_lead, lead_time_curve
from ..ml.validation import flood as flood_ds
from ..ml.validation.run import fit_logistic, predict
from .fani import FaniCase, load_amphan, load_fani

#: Forecast cutoffs (hours before landfall) scored per storm.
DEFAULT_LEADS = (72, 48, 24)
#: Dispatch threshold for the risk trajectory (matches the evacuation layer).
THRESHOLD = 0.5


@dataclass
class ZoneProfile:
    """The real exposed zone scored for a storm (population + egress capacity)."""

    zone_id: str
    population: int
    road_egress_pph: float
    assisted_egress_pph: float


#: Real exposed zones (coastal districts in the documented evacuation). Egress
#: capacities are rough planning references (OSM-order, not surveyed) — the same
#: caveat the clearance model already carries; the backtest leads with the
#: decision *structure*, not these magnitudes.
ZONES: dict[str, ZoneProfile] = {
    "FANI": ZoneProfile("puri-coastal-odisha", 200_500, 4_000.0, 600.0),
    "AMPHAN": ZoneProfile("south-24-parganas-wb", 250_000, 4_000.0, 600.0),
}


# --------------------------------------------------------------- validated reliability
def validated_far_by_lead(leads: tuple[int, ...] = DEFAULT_LEADS, *, cap: int = 5000) -> dict[int, float]:
    """FAR at each lead (hours) from Session A's VALIDATED flood lead-time curve.

    Trains the deterministic flood detector per horizon on real GloFAS data and
    returns the measured false-alarm rate at each lead — the real reliability the
    trajectory carries into the decision. Capped for speed; still real data.
    """
    rows = flood_ds.load_rows()
    train, test = flood_ds.temporal_split(rows)
    step = max(1, len(train) // cap)
    train = train[::step]
    Xtr = [list(r.features) for r in train]
    Htr = [r.horizon_labels for r in train]
    Xte = [list(r.features) for r in test]
    Hte = [r.horizon_labels for r in test]

    def factory(X, y):
        m = fit_logistic(X, y, name="bt", epochs=40, balanced=True)
        return lambda Xq: predict(m, Xq)

    curve = lead_time_curve(
        Xtr, Htr, Xte, Hte, [h * 24 for h in flood_ds.HORIZONS], factory, target_pod=0.9
    )
    far = far_by_lead(curve)  # keyed by hours
    # nearest-available FAR for each requested lead
    out: dict[int, float] = {}
    for lead in leads:
        if far:
            nearest = min(far, key=lambda h: abs(h - lead))
            out[lead] = far[nearest]
    return out


def approach_trajectory(
    case: FaniCase,
    lead: int,
    far_map: dict[int, float],
    *,
    leads: tuple[int, ...] = DEFAULT_LEADS,
) -> RiskTrajectory:
    """Risk trajectory at forecast cutoff ``lead`` h before landfall.

    ``p_event`` rises as landfall nears (a transparent monotone proxy for IMD's
    dynamical forecast — NOT a skillful storm model), gated on the storm being a
    real cyclone alert (>=34 kt) at the cutoff. ``far`` per horizon is Session
    A's VALIDATED reliability. Strictly leak-free: only pre-cutoff track is read.
    """
    landfall = case.landfall_point()
    from .replay import _parse  # reuse the replay time helpers

    lf_dt = _parse(landfall.time)
    cutoff_iso = (lf_dt - __import__("datetime").timedelta(hours=lead)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    before = case.points_before(cutoff_iso)
    cutoff_wind = before[-1].wind_kt if before else None
    is_alert = bool(cutoff_wind and cutoff_wind >= 34.0)

    horizons: list[Horizon] = []
    for h in sorted(leads, reverse=True):
        if h < lead:
            continue  # only horizons at/after this cutoff are knowable now
        # sharpening proxy: closer to landfall -> higher p_event, alert-gated.
        nearness = 1.0 - (h / (max(leads) + 24))
        p = round((0.35 + 0.6 * nearness) if is_alert else 0.1 * nearness, 4)
        horizons.append(Horizon(lead_hours=h, p_event=p, far=far_map.get(h)))
    return RiskTrajectory(
        location_id=ZONES[case.storm].zone_id,
        issued_at=cutoff_iso,
        horizons=horizons,
        threshold=THRESHOLD,
    )


# --------------------------------------------------------------------------- scoring
def _evac_lower_bound(evacuated: str) -> int | None:
    """Parse the low end of a documented '~1.2-1.5 million' evacuation string."""
    import re

    text = evacuated.lower().replace(",", "")
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return None
    val = float(nums[0])
    if "million" in text or " m" in text:
        val *= 1_000_000
    return int(val)


@dataclass
class EventBacktest:
    storm: str
    season: int
    landfall_place: str
    landfall_intensity: str
    documented_deaths: int
    documented_evacuated: str
    cutoff_lead_hours: int
    recommendation: str
    actionable_lead_hours: int
    must_issue_by_hours: float
    expected_moved: int
    zone_population: int
    equity_ok: bool
    stranded_cohorts: list[str]
    ordered_with_lead: bool  # recommended an order with usable lead
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


def backtest_event(
    case: FaniCase, far_map: dict[int, float], lead: int = 72
) -> EventBacktest:
    """Run + score the whole chain for one storm at one forecast cutoff."""
    zone = ZONES[case.storm]
    traj = approach_trajectory(case, lead, far_map)
    decision = decide_zone_evacuation(
        zone.zone_id, zone.population, zone.road_egress_pph, zone.assisted_egress_pph, traj
    )
    out = case.outcome
    ordered = decision.recommendation in (ORDER_BY_DEADLINE, NOT_CLEARABLE_VERTICAL)
    notes = [
        "Reliability (FAR/lead) is Session A's VALIDATED flood curve; the p_event "
        "approach path is a labelled proxy for IMD's dynamical forecast, not a "
        "skillful storm model.",
        f"Reality: {out.get('evacuated')} evacuated, {out.get('deaths')} deaths at a "
        f"{out.get('landfall_intensity')} landfall.",
    ]
    if decision.recommendation == NOT_CLEARABLE_VERTICAL:
        notes.append(
            "Recommendation is ORDER NOW + VERTICAL EVACUATION: with default "
            "planning egress, road+bus clearance of this coastal zone exceeds the "
            f"{decision.actionable_lead_hours} h lead — which is exactly why Odisha's "
            "real strategy leans on pre-built cyclone shelters, not full road "
            "evacuation. The hindcast reaches the same protective posture reality used."
        )
    elif decision.recommendation == ORDER_BY_DEADLINE:
        notes.append(
            f"Recommendation: ISSUE ORDER by T-{decision.must_issue_by_hours:.0f}h — "
            "consistent with the multi-day pre-cyclone evacuation reality ran."
        )
    if not decision.equity_ok:
        notes.append(
            "Equity gap surfaced: "
            + ", ".join(decision.stranded_cohorts)
            + " — the same finding the standalone vulnerability audit reports."
        )
    return EventBacktest(
        storm=case.storm,
        season=case.season,
        landfall_place=out.get("landfall_place", ""),
        landfall_intensity=out.get("landfall_intensity", ""),
        documented_deaths=int(out.get("deaths") or 0),
        documented_evacuated=str(out.get("evacuated", "")),
        cutoff_lead_hours=lead,
        recommendation=decision.recommendation,
        actionable_lead_hours=decision.actionable_lead_hours,
        must_issue_by_hours=decision.must_issue_by_hours,
        expected_moved=decision.expected_moved,
        zone_population=zone.population,
        equity_ok=decision.equity_ok,
        stranded_cohorts=decision.stranded_cohorts,
        ordered_with_lead=ordered and decision.actionable_lead_hours > 0,
        notes=notes,
    )


def run_backtest(lead: int = 72) -> dict:
    """Score the whole chain across all committed real cyclones at cutoff ``lead``."""
    far_map = validated_far_by_lead()
    cases = [load_fani(), load_amphan()]
    events = [backtest_event(c, far_map, lead=lead) for c in cases]
    n_ordered = sum(1 for e in events if e.ordered_with_lead)
    return {
        "methodology": "full-pipeline replay on real cyclones; validated FAR/lead "
        "reliability; labelled p_event proxy; scored vs documented outcome",
        "cutoff_lead_hours": lead,
        "validated_far_by_lead": far_map,
        "events": [e.to_dict() for e in events],
        "n_events": len(events),
        "n_ordered_with_lead": n_ordered,
    }


def to_markdown(report: dict) -> str:
    lines = [
        "# Full-Pipeline Historical Backtest (shadow mode on the past)",
        "",
        f"_{report['methodology']}_",
        "",
        f"Forecast cutoff: **{report['cutoff_lead_hours']} h before landfall** · "
        f"validated FAR/lead: "
        + ", ".join(f"{k}h={v:.0%}" for k, v in report["validated_far_by_lead"].items()),
        "",
        "| Storm | Landfall | Recommendation | Actionable lead | Protective action | "
        "Equity | Documented reality |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in report["events"]:
        equity = "✅" if e["equity_ok"] else "❌ " + ", ".join(e["stranded_cohorts"])
        reality = f"{e['documented_evacuated']}, {e['documented_deaths']} deaths"
        if e["recommendation"] == "NOT_CLEARABLE_VERTICAL":
            action = "order now + vertical shelter (clearance > lead)"
        elif e["recommendation"] == ORDER_BY_DEADLINE:
            action = f"issue by T-{e['must_issue_by_hours']:.0f}h"
        else:
            action = "monitor"
        lines.append(
            f"| {e['storm']} {e['season']} | {e['landfall_place']} | {e['recommendation']} | "
            f"{e['actionable_lead_hours']} h | {action} | {equity} | {reality} |"
        )
    lines += [
        "",
        f"- **{report['n_ordered_with_lead']}/{report['n_events']}** storms: the chain "
        "recommended an evacuation order with usable lead — consistent with the real "
        "large-scale evacuations that kept tolls low for these very severe landfalls.",
        "",
        "## Honest limits",
        "- FAR/lead reliability is VALIDATED (real GloFAS flood curve); the per-storm "
        "p_event path is a transparent proxy for IMD's forecast, not a storm model.",
        "- Egress capacities and compliance are UNVALIDATED planning assumptions; the "
        "value is the decision structure and the equity finding, not the exact counts.",
        "- This is hindcast on documented events, not a live shadow season — the "
        "institutional gate still requires real-time prediction and independent review.",
    ]
    return "\n".join(lines)
