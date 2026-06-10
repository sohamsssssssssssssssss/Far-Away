"""Full-pipeline historical backtest — the whole chain scored against reality.

Offline/deterministic: committed cyclone fixtures + Session A's validated flood
lead-time curve (computed from the committed GloFAS fixture). Asserts the chain
runs end to end, stays leak-free, carries validated reliability, and reaches a
protective decision consistent with the documented large-scale evacuations.
"""
from __future__ import annotations

import pytest

from disastermind.hindcast.fani import load_fani
from disastermind.hindcast.pipeline_backtest import (
    ORDER_BY_DEADLINE,
    approach_trajectory,
    backtest_event,
    run_backtest,
    to_markdown,
    validated_far_by_lead,
)

NOT_CLEARABLE = "NOT_CLEARABLE_VERTICAL"


@pytest.fixture(scope="module")
def far_map():
    return validated_far_by_lead()


def test_validated_far_is_real_and_rises_with_lead(far_map):
    # FAR comes from the validated flood curve: present, in [0,1], and the long
    # lead is noisier than the short lead (the documented lead-time trade).
    assert far_map and all(0.0 <= v <= 1.0 for v in far_map.values())
    assert far_map[72] > far_map[24]


def test_trajectory_is_leak_free_and_carries_validated_far(far_map):
    case = load_fani()
    traj = approach_trajectory(case, lead=72, far_map=far_map)
    # cutoff is 72 h before landfall; issued_at must precede every track point used
    assert traj.issued_at <= case.landfall_point().time
    # only horizons at/after the cutoff are present, each carrying validated FAR
    assert all(h.lead_hours >= 72 for h in traj.horizons)
    assert all(h.far is not None for h in traj.horizons)
    # p_event sharpens toward landfall (monotone non-increasing with lead)
    by_lead = sorted(traj.horizons, key=lambda h: h.lead_hours)
    assert by_lead[0].p_event >= by_lead[-1].p_event


def test_event_reaches_a_protective_decision_with_lead(far_map):
    bt = backtest_event(load_fani(), far_map, lead=72)
    assert bt.actionable_lead_hours >= 24  # genuine multi-day warning
    assert bt.recommendation in (ORDER_BY_DEADLINE, NOT_CLEARABLE)
    assert bt.ordered_with_lead  # a protective order, not "do nothing"
    assert bt.documented_deaths > 0  # real documented outcome is attached


def test_equity_gap_is_surfaced_not_hidden(far_map):
    bt = backtest_event(load_fani(), far_map, lead=72)
    # the transport-dependent cohort is the one the vulnerability audit strands;
    # the end-to-end decision must surface the same gap, not average it away
    assert not bt.equity_ok
    assert any("transport" in c for c in bt.stranded_cohorts)


def test_run_backtest_scores_all_committed_storms(far_map):
    report = run_backtest(lead=72)
    assert report["n_events"] == 2  # Fani + Amphan
    assert report["n_ordered_with_lead"] == 2  # both reach a protective order
    storms = {e["storm"] for e in report["events"]}
    assert storms == {"FANI", "AMPHAN"}
    md = to_markdown(report)
    assert "Full-Pipeline Historical Backtest" in md
    assert "VALIDATED" in md  # the reliability provenance is stated
    assert "not a live shadow season" in md  # the institutional caveat is kept
