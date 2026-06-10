"""End-to-end smoke of the multi-hazard validation orchestrator (fast knobs)."""
from __future__ import annotations

import json

import pytest

from disastermind.ml.validation.run import (
    HAZARDS,
    evaluate_hazard,
    fire_spec,
    run_validation,
    to_markdown,
)


@pytest.fixture(scope="module")
def fire_report():
    """One full engine pass on the real fire dataset with test-speed knobs."""
    return evaluate_hazard(
        fire_spec(),
        epochs=25,
        cv_epochs=10,
        fit_cap=2500,
        cv_cap=1200,
        n_boot=40,
        tail_boot=30,
    )


def test_report_carries_every_evidence_section(fire_report):
    for key in (
        "model",
        "baseline_comparisons",
        "decision",
        "calibration",
        "conformal",
        "fairness",
        "tail",
        "cv_leave_one_region_out",
        "cv_rolling_origin",
        "drift",
        "retrain_decision",
    ):
        assert key in fire_report, f"missing evidence section {key}"
    assert fire_report["model"]["auc"] > 0.75  # real skill even at test knobs


def test_threshold_is_chosen_on_calibration_not_test(fire_report):
    d = fire_report["decision"]
    # the threshold exists and the test-set confusion was evaluated AT it
    assert 0.0 <= d["threshold_from_calibration"] <= 1.0
    assert d["test_at_target_pod"]["threshold"] == d["threshold_from_calibration"]


def test_baseline_comparison_has_significance_machinery(fire_report):
    comp = fire_report["baseline_comparisons"]["angstrom_index"]["auc"]
    assert set(comp) >= {"model", "baseline", "delta_ci95", "p_value", "significant_at_5pct"}


def test_report_is_json_serialisable_and_renders(fire_report):
    blob = {"methodology": "m", "hazards": {"fire": fire_report}}
    json.dumps(blob)  # no exotic types anywhere
    md = to_markdown(blob)
    assert "## Fire" in md
    assert "operational baselines" in md
    assert "Fairness audit" in md


def test_run_validation_rejects_unknown_hazard():
    with pytest.raises(ValueError):
        run_validation(["volcano"])


def test_all_three_hazards_are_registered():
    assert set(HAZARDS) == {"earthquake", "flood", "fire"}
