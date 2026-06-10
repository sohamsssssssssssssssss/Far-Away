"""Dissemination + warning-response gap — offline, deterministic.

Covers compliance as a function of (lead, FAR) with a compliance-optimal lead
that is NOT the longest, the per-cohort reach gap (double jeopardy), and the
reason-breakdown of who is left behind. All magnitudes are unvalidated planning
assumptions; the tests assert SHAPE and structure, not real compliance rates.
"""
from __future__ import annotations

import json
import os

from disastermind.evacuation import (
    Horizon,
    RiskTrajectory,
    assess_dissemination,
    combined_reach,
    compliance_curve,
    compliance_given_reached,
    far_at_lead,
    plan_phased_evacuation,
)
from disastermind.evacuation.dissemination import trust_factor

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CAP = os.path.join(_HERE, "disastermind", "hindcast", "fixtures", "puri_capacity.json")


def _pop() -> int:
    return int(json.load(open(_CAP))["population_tags"][0][1])


# ----------------------------------------------------------------- contract (FAR)
def test_horizon_far_is_additive_and_roundtrips():
    d = {"location_id": "z", "issued_at": "t",
         "horizons": [{"lead_hours": 24, "p_event": 0.8, "far": 0.44},
                      {"lead_hours": 12, "p_event": 0.6}],  # far omitted -> None
         "threshold": 0.5}
    t = RiskTrajectory.from_dict(d)
    assert t.horizons[0].far == 0.44 and t.horizons[1].far is None
    assert t.to_dict() == d  # exact (far only emitted when present)
    assert far_at_lead(t, 20) == 0.44  # nearest horizon with FAR


# --------------------------------------------------------------- compliance shape
def test_compliance_rises_with_lead_falls_with_far():
    assert compliance_given_reached(0, 0.0) == 0.0  # no warning -> nobody acts
    assert compliance_given_reached(72, 0.2) > compliance_given_reached(12, 0.2)  # more lead
    assert compliance_given_reached(48, 0.8) < compliance_given_reached(48, 0.2)  # more FAR -> less trust


def test_false_alarm_fatigue_lowers_trust():
    assert trust_factor(0.4, prior_false_alarms=3) < trust_factor(0.4, prior_false_alarms=0)


def test_compliance_optimal_lead_is_not_the_longest():
    """The key finding: FAR climbing with lead creates a mid-range sweet spot."""
    t = RiskTrajectory("z", "t", [
        Horizon(6, 0.85, far=0.30), Horizon(24, 0.8, far=0.44),
        Horizon(48, 0.7, far=0.60), Horizon(72, 0.65, far=0.72),
        Horizon(168, 0.6, far=0.87)], threshold=0.5)
    opt = compliance_curve(t)
    assert opt.far_supplied
    assert 0 < opt.optimal_lead_hours < 168  # not the longest horizon
    assert opt.optimal_compliance == max(c for _l, _f, c in opt.curve)


# --------------------------------------------------------------- the reach gap
def test_phone_channels_reach_the_vulnerable_less_than_general():
    phone = ("cell_broadcast", "sms")
    nv = combined_reach("transport-dependent (no motor vehicle)", phone)
    eld = combined_reach("elderly/mobility-impaired", phone)
    gen = combined_reach("general (self-evacuating)", phone)
    assert nv < gen and eld < gen  # the access gap (no smartphone / language)
    # adding door-to-door wardens closes much of the gap
    assert combined_reach("elderly/mobility-impaired", phone + ("community_warden",)) > eld


# ----------------------------------------------------- end-to-end reason breakdown
def test_assessment_reason_breakdown_accounts_for_everyone():
    flash = RiskTrajectory("puri", "t", [Horizon(18, 0.8, far=0.40)], 0.5)
    plan = plan_phased_evacuation(_pop(), 8000.0, 4000.0, flash)
    a = assess_dissemination(plan, flash, active_channels=("cell_broadcast", "sms"))
    for c in a.cohorts:
        # moved + not_reached + noncompliant + stranded == population (±rounding)
        assert abs((c.moved + c.not_reached + c.noncompliant + c.stranded) - c.population) <= 2
    # double jeopardy: vulnerable cohorts have more not-reached share than general
    nv = next(c for c in a.cohorts if "no motor" in c.name)
    gen = next(c for c in a.cohorts if c.name.startswith("general"))
    assert nv.not_reached / nv.population > gen.not_reached / gen.population


def test_deterministic():
    flash = RiskTrajectory("puri", "t", [Horizon(18, 0.8, far=0.40)], 0.5)
    plan = plan_phased_evacuation(_pop(), 8000.0, 4000.0, flash)
    a = assess_dissemination(plan, flash).to_dict()
    b = assess_dissemination(plan, flash).to_dict()
    assert a == b
