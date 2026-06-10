"""Integration capstone + cost/benefit + contraflow — offline, deterministic.

Covers the four recommendation paths, the evacuation cost/benefit break-even
(evacuation-itself-kills / false-alarm guard), the contraflow capacity lever, and
the accountability decision record. Real Puri population fixture.
"""
from __future__ import annotations

import json
import os

from disastermind.evacuation import (
    BELOW_BREAKEVEN_HOLD,
    NO_ACTIONABLE_WARNING,
    NOT_CLEARABLE_VERTICAL,
    ORDER_BY_DEADLINE,
    Horizon,
    RiskTrajectory,
    break_even_probability,
    contraflow_egress,
    decide_zone_evacuation,
    evacuation_tradeoff,
)
from disastermind.evacuation.clearance import DEFAULT_CONTRAFLOW_FACTOR

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CAP = os.path.join(_HERE, "disastermind", "hindcast", "fixtures", "puri_capacity.json")


def _pop() -> int:
    return int(json.load(open(_CAP))["population_tags"][0][1])


def _decide(traj, road=8000.0, assisted=4000.0):
    return decide_zone_evacuation("Puri", _pop(), road, assisted, traj)


# --------------------------------------------------------------------- contraflow
def test_contraflow_boosts_egress_but_not_double():
    base = 8000.0
    cf = contraflow_egress(base)
    assert cf == round(base * DEFAULT_CONTRAFLOW_FACTOR, 1)
    assert base < cf < 2 * base  # ~1.8x, not 2x (merge/termination losses)


# ------------------------------------------------------------- cost / break-even
def test_break_even_probability_and_net_harm_at_low_p():
    be = break_even_probability(0.001, 0.02)  # evac rate / stay fatality
    assert abs(be - 0.05) < 1e-9
    # at P below break-even, moving a cohort is net-harmful
    t_low = evacuation_tradeoff({"general (self-evacuating)": 10000}, p_event=0.02,
                                stay_fatality_rate=0.02)
    assert t_low.cohorts[0].net_harmful_at_p is True
    t_high = evacuation_tradeoff({"general (self-evacuating)": 10000}, p_event=0.9,
                                 stay_fatality_rate=0.02)
    assert t_high.cohorts[0].net_harmful_at_p is False


def test_fragile_cohorts_have_higher_break_even():
    t = evacuation_tradeoff(
        {"hospitalised/medical": 1000, "general (self-evacuating)": 1000}, p_event=0.5,
    )
    by = {c.name: c for c in t.cohorts}
    assert by["hospitalised/medical"].break_even_p > by["general (self-evacuating)"].break_even_p


def test_false_alarm_returns_cry_wolf_increment():
    t = evacuation_tradeoff({"general (self-evacuating)": 1000}, p_event=0.3,
                            false_alarm_threshold=0.5)
    assert t.cry_wolf_increment == 1  # below threshold -> erodes future compliance


# ---------------------------------------------------------- recommendation paths
def test_long_lead_high_p_orders_by_deadline():
    traj = RiskTrajectory("puri", "t", [Horizon(72, 0.8, far=0.5), Horizon(24, 0.95, far=0.4)], 0.5)
    d = _decide(traj)
    assert d.recommendation == ORDER_BY_DEADLINE
    assert d.must_issue_by_hours > 0 and d.net_lives_saved > 0


def test_short_lead_is_not_clearable_vertical():
    traj = RiskTrajectory("puri", "t", [Horizon(12, 0.85, far=0.35)], 0.5)
    d = _decide(traj)
    assert d.recommendation == NOT_CLEARABLE_VERTICAL
    assert d.equity_ok is False  # the vulnerable are stranded


def test_low_probability_holds_below_breakeven():
    traj = RiskTrajectory("puri", "t", [Horizon(96, 0.06, far=0.9)], 0.05)
    d = _decide(traj)
    assert d.recommendation == BELOW_BREAKEVEN_HOLD  # over-evacuation guard


def test_no_threshold_crossing_is_no_actionable_warning():
    traj = RiskTrajectory("puri", "t", [Horizon(48, 0.2, far=0.5)], 0.5)  # never crosses
    d = _decide(traj)
    assert d.recommendation == NO_ACTIONABLE_WARNING
    assert d.actionable_lead_hours == 0


# --------------------------------------------------------------- decision record
def test_decision_record_is_accountable_and_complete():
    traj = RiskTrajectory("puri", "2019-05-01T00:00Z", [Horizon(72, 0.8, far=0.5)], 0.5)
    d = _decide(traj)
    r = d.decision_record
    assert set(r) >= {"what_we_knew", "what_we_recommended", "who_is_left_behind_and_why",
                      "equity", "cost_benefit", "caveats"}
    assert r["what_we_knew"]["forecast_issued_at"] == "2019-05-01T00:00Z"
    assert r["what_we_recommended"]["recommendation"] == d.recommendation
    assert r["caveats"]  # honest limits always present


def test_deterministic():
    traj = RiskTrajectory("puri", "t", [Horizon(48, 0.7, far=0.5)], 0.5)
    assert _decide(traj).to_dict() == _decide(traj).to_dict()
