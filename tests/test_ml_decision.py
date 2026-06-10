"""Decision-point metrics (POD/FAR/CSI, operating points, cost model)."""
from __future__ import annotations

import pytest

from disastermind.ml.eval.decision import (
    Confusion,
    confusion_at,
    decision_report,
    operating_point_for_pod,
    operating_point_min_cost,
)

Y = [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]
P = [0.9, 0.8, 0.6, 0.2, 0.7, 0.4, 0.3, 0.2, 0.1, 0.05]


# ----------------------------------------------------------------- contingency
def test_confusion_counts_and_scores():
    c = confusion_at(Y, P, 0.5)
    assert (c.tp, c.fp, c.fn, c.tn) == (3, 1, 1, 5)
    assert c.pod == 3 / 4
    assert c.far == 1 / 4
    assert c.pofd == 1 / 6
    assert c.csi == 3 / 5
    assert c.bias == 1.0
    assert c.n == len(Y)


def test_hss_zero_for_chance_and_one_for_perfect():
    perfect = confusion_at([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2], 0.5)
    assert perfect.hss == 1.0
    # constant prediction => no skill over chance
    chance = confusion_at([1, 0, 1, 0], [0.6, 0.6, 0.6, 0.6], 0.5)
    assert chance.hss == 0.0


def test_degenerate_empty_is_safe():
    c = confusion_at([], [], 0.5)
    assert isinstance(c, Confusion)
    assert c.pod == c.far == c.csi == 0.0


# ------------------------------------------------------------- operating points
def test_operating_point_meets_target_pod_with_max_threshold():
    c = operating_point_for_pod(Y, P, target_pod=0.75)
    assert c.pod >= 0.75
    # any higher distinct threshold would drop POD below target
    higher = [t for t in sorted(set(P)) if t > c.threshold]
    for t in higher:
        assert confusion_at(Y, P, t).pod < 0.75


def test_operating_point_falls_back_to_alarm_on_everything():
    # an inverted model can only reach POD 1.0 by alerting on every row
    c = operating_point_for_pod([1, 0], [0.1, 0.9], target_pod=1.0)
    assert c.pod == 1.0
    assert c.threshold <= 0.1


def test_operating_point_rejects_bad_target():
    with pytest.raises(ValueError):
        operating_point_for_pod(Y, P, target_pod=0.0)


def test_min_cost_tradeoff_moves_with_costs():
    # misses catastrophic -> low threshold (catch everything)
    c_miss, _ = operating_point_min_cost(Y, P, miss_cost=1000.0, false_alarm_cost=1.0)
    # false alarms catastrophic -> high threshold (alarm rarely)
    c_fa, _ = operating_point_min_cost(Y, P, miss_cost=1.0, false_alarm_cost=1000.0)
    assert c_miss.threshold <= c_fa.threshold
    assert c_miss.pod >= c_fa.pod


def test_min_cost_is_exact_minimum_over_thresholds():
    best, cost = operating_point_min_cost(Y, P, miss_cost=10.0, false_alarm_cost=1.0)
    for t in sorted(set(P)) + [0.0]:
        c = confusion_at(Y, P, t)
        assert cost <= c.fn * 10.0 + c.fp * 1.0


def test_decision_report_is_json_shaped():
    rep = decision_report(Y, P, target_pod=0.75, miss_cost=50, false_alarm_cost=2)
    assert rep["at_target_pod"]["pod"] >= 0.75
    assert rep["cost_assumptions"] == {"miss": 50, "false_alarm": 2}
    assert rep["min_cost"]["total_cost"] >= 0
