"""Clearance-time evacuation decision — offline, deterministic.

Covers the risk-trajectory contract, the clearance-time arithmetic, and the
core decision: feasible (long-lead cyclone) vs NOT-CLEARABLE (short-lead flood,
the critical flag), plus the egress sensitivity. Real Puri population fixture.
"""
from __future__ import annotations

import collections
import json
import os

from disastermind.evacuation import (
    Horizon,
    RiskTrajectory,
    actionable_lead_hours,
    decide,
    egress_capacity_from_roads,
    estimate_clearance,
)
from disastermind.evacuation.clearance import clearance_sensitivity

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CAP = os.path.join(_HERE, "disastermind", "hindcast", "fixtures", "puri_capacity.json")
_OSM = os.path.join(_HERE, "disastermind", "hindcast", "fixtures", "puri_osm.json")


def _puri_population() -> int:
    return int(json.load(open(_CAP))["population_tags"][0][1])


# --------------------------------------------------------------- risk trajectory
def test_actionable_lead_is_longest_crossing_horizon():
    t = RiskTrajectory("z", "2019-05-01T00:00:00Z",
                       [Horizon(72, 0.55), Horizon(48, 0.7), Horizon(24, 0.9)], threshold=0.5)
    assert actionable_lead_hours(t) == 72  # longest lead that clears the threshold


def test_no_actionable_warning_when_nothing_crosses():
    t = RiskTrajectory("z", "x", [Horizon(72, 0.1), Horizon(24, 0.3)], threshold=0.5)
    assert actionable_lead_hours(t) == 0  # accurate only at t+0 => useless


def test_trajectory_dict_roundtrip_is_the_contract_shape():
    d = {"location_id": "puri", "issued_at": "2020-05-18T00:00:00Z",
         "horizons": [{"lead_hours": 48, "p_event": 0.6}], "threshold": 0.5}
    t = RiskTrajectory.from_dict(d)
    assert t.to_dict() == d  # exact agreed schema


# ------------------------------------------------------------------- clearance
def test_clearance_arithmetic_is_explicit_and_deterministic():
    est = estimate_clearance(100000, 10000.0, participation=0.9,
                             mobilization_hours=4.0, last_mile_hours=0.75)
    # T = mobilization + demand/egress + last_mile = 4 + (90000/10000) + 0.75
    assert abs(est.clearance_hours - (4.0 + 9.0 + 0.75)) < 1e-6
    assert est.egress_hours == 9.0


def test_egress_from_roads_scales_with_real_road_counts():
    fx = json.load(open(_OSM))
    rc = dict(collections.Counter(w["highway"] for w in fx["roads"]))
    e = egress_capacity_from_roads(rc)
    assert e > 0
    # more major roads -> more egress (monotonic in the inputs)
    assert egress_capacity_from_roads({"trunk": 100}) > egress_capacity_from_roads({"trunk": 10})


# -------------------------------------------------------------------- decision
def test_long_lead_cyclone_is_feasible_with_slack():
    pop = _puri_population()
    traj = RiskTrajectory("puri", "2019-05-01T00:00:00Z",
                          [Horizon(72, 0.55), Horizon(24, 0.9)], threshold=0.5)
    est = estimate_clearance(pop, 20000.0)
    d = decide(est, traj)
    assert d.feasible is True
    assert d.must_issue_by_hours == est.clearance_hours  # issue by T-minus-clearance
    assert d.slack_hours > 0


def test_short_lead_flood_is_not_clearable_the_critical_flag():
    """A flash flood with only 6 h warning cannot empty a large zone -> flagged."""
    pop = _puri_population()
    traj = RiskTrajectory("puri", "2020-05-18T00:00:00Z",
                          [Horizon(6, 0.8), Horizon(12, 0.3)], threshold=0.5)  # only t+6 crosses
    est = estimate_clearance(pop, 5000.0)  # modest egress
    d = decide(est, traj)
    assert actionable_lead_hours(traj) == 6
    assert d.feasible is False
    assert d.slack_hours < 0  # negative => un-clearable shortfall
    assert "NOT CLEARABLE" in d.message


def test_sensitivity_flips_feasibility_with_egress():
    pop = _puri_population()
    traj = RiskTrajectory("puri", "x", [Horizon(72, 0.6)], threshold=0.5)
    sens = clearance_sensitivity(pop, [2000.0, 40000.0], traj)
    feas = {e: ok for e, _c, ok in sens}
    assert feas[2000.0] is False and feas[40000.0] is True  # low egress -> infeasible
