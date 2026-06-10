"""Fairness subgroup audit + rare-severe tail evaluation."""
from __future__ import annotations

from disastermind.ml.eval.fairness import (
    audit_subgroups,
    equalized_thresholds,
    remediate,
)
from disastermind.ml.eval.tail import SeveritySlice, tail_report


# ------------------------------------------------------------------- fairness
def _two_group_data():
    """Group 'b' systematically scored lower on its real events than group 'a'."""
    y, p, g = [], [], []
    for _ in range(60):
        y += [1, 0]
        p += [0.9, 0.1]
        g += ["a", "a"]
    for _ in range(60):
        y += [1, 0]
        p += [0.3, 0.1]  # real events score under the shared threshold
        g += ["b", "b"]
    return y, p, g


def test_under_protected_group_is_flagged():
    y, p, g = _two_group_data()
    audit = audit_subgroups(y, p, g, threshold=0.5)
    assert audit["under_protected_groups"] == ["b"]
    assert not audit["passed"]
    rows = {r["group"]: r for r in audit["groups"]}
    assert rows["a"]["pod"] == 1.0
    assert rows["b"]["pod"] == 0.0 and rows["b"]["under_protected"]


def test_equal_groups_pass():
    y = [1, 0, 1, 0] * 30
    p = [0.9, 0.1, 0.9, 0.1] * 30
    g = ["a", "a", "b", "b"] * 30
    audit = audit_subgroups(y, p, g, threshold=0.5)
    assert audit["passed"] and audit["under_protected_groups"] == []


def test_tiny_groups_do_not_noise_flag():
    y, p, g = _two_group_data()
    # one stray bad row in a 2-member group must not trigger a flag (min_n)
    y += [1, 0]
    p += [0.1, 0.1]
    g += ["tiny", "tiny"]
    audit = audit_subgroups(y, p, g, threshold=0.5, min_n=30)
    assert "tiny" not in audit["under_protected_groups"]


def test_group_aware_thresholds_close_a_revealed_gap_at_a_far_cost():
    """The remediation: a group whose events score lower clears the bar once it
    gets its own (lower) threshold — and the FAR cost of that is reported."""
    # group 'a' well-separated; group 'b' events score lower (need lower cutoff)
    y, p, g = [], [], []
    for _ in range(80):
        y += [1, 0]
        p += [0.9, 0.1]
        g += ["a", "a"]
    for _ in range(80):
        y += [1, 0]
        p += [0.45, 0.1]  # real events fall below the 0.5 global threshold
        g += ["b", "b"]

    before = audit_subgroups(y, p, g, threshold=0.5)
    assert "b" in before["under_protected_groups"]  # gap revealed at global threshold

    gthr = equalized_thresholds(y, p, g, target_pod=0.9, fallback=0.5)
    assert gthr["b"] < 0.5  # b needs a lower operating point
    rem = remediate(y, p, g, threshold=0.5, target_pod=0.9, group_thresholds=gthr)
    assert rem["after"]["passed"]  # gap closed
    assert rem["far_cost_of_equity"]["b"] >= 0  # the price of equity is stated


def test_remediation_classifies_a_discrimination_deficit():
    """A group the model can't RANK (low AUC) is flagged as needing better inputs,
    not a threshold — a threshold can't fix weak discrimination."""
    import random

    rng = random.Random(0)
    y, p, g = [], [], []
    for _ in range(200):  # group 'a': strong signal
        lab = 1 if rng.random() < 0.3 else 0
        y.append(lab)
        p.append(0.8 * lab + 0.2 * rng.random())
        g.append("a")
    for _ in range(200):  # group 'b': scores are noise vs labels (AUC ~0.5)
        y.append(1 if rng.random() < 0.3 else 0)
        p.append(rng.random())
        g.append("b")
    gthr = equalized_thresholds(y, p, g, target_pod=0.9, fallback=0.5)
    rem = remediate(y, p, g, threshold=0.5, target_pod=0.9, group_thresholds=gthr)
    if "b" in rem["after"]["under_protected_groups"]:
        assert "discrimination deficit" in rem["residual_cause"]["b"]


def test_eventless_group_reports_null_pod_not_a_flag():
    y = [1, 0] * 40 + [0] * 40
    p = [0.9, 0.1] * 40 + [0.1] * 40
    g = ["a", "a"] * 40 + ["quiet"] * 40
    audit = audit_subgroups(y, p, g, threshold=0.5)
    quiet = next(r for r in audit["groups"] if r["group"] == "quiet")
    assert quiet["pod"] is None and not quiet["under_protected"]


# ----------------------------------------------------------------------- tail
def test_tail_slices_report_pod_with_intervals():
    y = [1] * 40 + [0] * 160
    p = [0.9] * 30 + [0.2] * 10 + [0.1] * 160  # 30/40 events detected at 0.5
    sev = [{"mag": 7.5}] * 20 + [{"mag": 5.0}] * 20 + [{"mag": 4.0}] * 160
    rep = tail_report(
        y, p, sev,
        [SeveritySlice("M7+", lambda s: s["mag"] >= 7.0)],
        threshold=0.5, n_boot=100, seed=0,
    )
    s = rep["slices"][0]
    assert s["events"] == 20
    assert s["pod"] == 1.0  # all M7+ events were in the well-scored block
    assert s["pod_ci95"][0] <= s["pod"] <= s["pod_ci95"][1]
    assert s["auc_severe_vs_rest"] > 0.9


def test_empty_slice_is_reported_not_hidden():
    y, p = [1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2]
    sev = [{"mag": 5.0}] * 4
    rep = tail_report(
        y, p, sev,
        [SeveritySlice("M8+", lambda s: s["mag"] >= 8.0)],
        threshold=0.5, n_boot=50, seed=0,
    )
    s = rep["slices"][0]
    assert s["events"] == 0 and s["pod"] is None and s["auc_severe_vs_rest"] is None
