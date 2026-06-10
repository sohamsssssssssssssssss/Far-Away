"""Evacuation cost/benefit — because evacuation itself kills, and false alarms
compound.

Ordering a mass evacuation is not free: moving people causes casualties (traffic
accidents en route, and real harm to the medically fragile being moved), and if
the hazard does not arrive those casualties are pure loss — plus the cry-wolf
erosion of the next warning's compliance. So the decision is a TRADE, not a
reflex:

    expected lives saved by evacuating a cohort
        = moved x (stay_fatality_rate x P(event) - evac_casualty_rate)

Evacuation is net-positive only when ``stay_fatality_rate x P(event) >
evac_casualty_rate``. Below the **break-even probability**
``evac_casualty_rate / stay_fatality_rate`` the evacuation costs more lives than
it saves — the decision-theoretic core of the false-alarm problem. And because
the medically fragile carry a HIGHER evacuation-casualty rate (moving ICU
patients is dangerous), their break-even probability is higher: evacuating them
for a likely false alarm can kill them. That sharpens the equity picture from the
other side — the vulnerable are both hardest to move AND most harmed by a
needless move.

The dynamic/reputational cost model lives in Session A's lane; this module
contributes the evacuation-casualty + break-even trade and the cry-wolf feedback
(it returns the fatigue increment the dissemination model consumes via
``prior_false_alarms``).

*** HONESTY ***: every rate is an EXPLICIT, TUNABLE, UNVALIDATED planning
assumption. The value is the STRUCTURE (break-even, who-is-net-harmed), not the
absolute counts. Pure, deterministic, stdlib-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Per-cohort evacuation-casualty rate (fraction of THIS cohort's evacuees harmed
# en route / by the move itself). Fragile cohorts are higher. ASSUMPTIONS.
DEFAULT_EVAC_CASUALTY_RATE: dict[str, float] = {
    "hospitalised/medical": 0.0050,            # moving ICU / bedridden is dangerous
    "elderly/mobility-impaired": 0.0015,
    "transport-dependent (no motor vehicle)": 0.0008,
    "children-dependent households": 0.0006,
    "general (self-evacuating)": 0.0005,        # mostly road-accident risk
}
#: Default fraction of those who STAY in the danger zone who die if the hazard
#: hits without shelter. Hazard/zone-specific; a planning assumption.
DEFAULT_STAY_FATALITY_RATE = 0.02


@dataclass
class CohortTradeoff:
    name: str
    moved: int
    evac_casualty_rate: float
    stay_fatality_rate: float
    expected_lives_saved: float   # net of evacuation casualties
    evac_casualties: float
    break_even_p: float           # P(event) below which moving this cohort is net-harmful
    net_harmful_at_p: bool        # True if, at this P(event), the move costs net lives


@dataclass
class EvacuationTradeoff:
    p_event: float
    stay_fatality_rate: float
    cohorts: list[CohortTradeoff]
    total_lives_saved: float       # sum over cohorts (can be negative => net harm)
    total_evac_casualties: float
    cry_wolf_increment: int        # +1 prior-false-alarm if this proves a false alarm
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["cohorts"] = [c.__dict__ for c in self.cohorts]
        return d


def break_even_probability(evac_casualty_rate: float, stay_fatality_rate: float) -> float:
    """P(event) at which evacuating breaks even (saved == caused)."""
    if stay_fatality_rate <= 0:
        return 1.0
    return min(1.0, evac_casualty_rate / stay_fatality_rate)


def evacuation_tradeoff(
    moved_by_cohort: dict[str, int],
    p_event: float,
    *,
    stay_fatality_rate: float = DEFAULT_STAY_FATALITY_RATE,
    evac_casualty_rate: dict[str, float] | None = None,
    false_alarm_threshold: float = 0.5,
) -> EvacuationTradeoff:
    """Cost/benefit of the evacuation actually carried out, per cohort.

    ``moved_by_cohort`` is the number actually evacuated per cohort (from the
    dissemination assessment). ``p_event`` is the forecast probability the hazard
    arrives (from the risk trajectory at the decision lead).
    """
    rates = evac_casualty_rate or DEFAULT_EVAC_CASUALTY_RATE
    cohorts: list[CohortTradeoff] = []
    total_saved = 0.0
    total_cas = 0.0
    for name, moved in moved_by_cohort.items():
        ecr = rates.get(name, DEFAULT_EVAC_CASUALTY_RATE.get(name, 0.0005))
        saved = moved * (stay_fatality_rate * p_event - ecr)
        cas = moved * ecr
        be = break_even_probability(ecr, stay_fatality_rate)
        total_saved += saved
        total_cas += cas
        cohorts.append(CohortTradeoff(
            name=name, moved=moved, evac_casualty_rate=ecr,
            stay_fatality_rate=stay_fatality_rate,
            expected_lives_saved=round(saved, 1), evac_casualties=round(cas, 1),
            break_even_p=round(be, 4), net_harmful_at_p=p_event < be,
        ))
    # cry-wolf: a warning that does not verify (p below the dispatch threshold yet
    # we evacuated, or it simply did not occur) adds a prior false alarm, which the
    # dissemination model uses to erode the NEXT warning's compliance.
    cry_wolf = 1 if p_event < false_alarm_threshold else 0
    notes = [
        "Evacuation is net-positive only where stay-fatality x P(event) > "
        "evac-casualty rate; below each cohort's break-even P, the move costs net "
        "lives — the decision-theoretic core of the false-alarm problem.",
        "The medically fragile have the HIGHEST evac-casualty rate and thus the "
        "highest break-even P: evacuating them for a likely false alarm can kill "
        "them — the vulnerable are harmed from BOTH sides (hard to move, harmed by "
        "a needless move).",
        "If this verifies as a false alarm it returns cry_wolf_increment=1, which "
        "the dissemination model feeds into prior_false_alarms to erode future "
        "compliance — false alarms compound.",
        "ALL rates are UNVALIDATED planning assumptions; the value is the break-even "
        "structure, not the absolute casualty counts.",
    ]
    return EvacuationTradeoff(
        p_event=p_event, stay_fatality_rate=stay_fatality_rate, cohorts=cohorts,
        total_lives_saved=round(total_saved, 1), total_evac_casualties=round(total_cas, 1),
        cry_wolf_increment=cry_wolf, notes=notes,
    )
