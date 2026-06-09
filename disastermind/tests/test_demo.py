"""Tests for disastermind.demo — the narrated offline golden-path runner.

Stdlib + pytest only; fully offline and deterministic (the demo drives the
scenario generators with a frozen clock and a TemplateClient-backed advisor).
"""
from __future__ import annotations

import json

import pytest

from disastermind.core.contracts import Topic
from disastermind.demo import DEMO_MODULES, DemoTranscript, run_demo
from disastermind.demo.runner import run_demo as run_demo_direct


# --------------------------------------------------------------- required behaviour
def test_earthquake_reaches_dispatch_with_report_and_brief():
    """run_demo("B") reaches a DISPATCH and has report + brief sections."""
    t = run_demo("B")

    assert isinstance(t, DemoTranscript)
    assert t.module == "B"

    # (1) activation narrated via orchestration.triggers.should_activate
    assert t["activation"]["activated"] is True
    assert t["activation"]["decided"] == "B"

    # (3) the pipeline reached at least one real DISPATCH
    assert t["tally"]["dispatch"] >= 1
    assert t["tally"]["topics"].get(Topic.DISPATCH, 0) >= 1

    # (4) after-action report section present and non-trivial
    assert "markdown" in t["report"]
    assert "After-Action Report" in t["report"]["markdown"]
    assert t["report"]["dict"]["incident_id"] == t.incident_id

    # (5) commander situation brief section present
    assert "SITUATION BRIEF" in t["brief"]

    # earthquake is not a cyclone -> no public alert
    assert t["public_alert"] == []


def test_cyclone_escalate_surfaces_escalation_in_tally():
    """run_demo("A", escalate=True) surfaces an ESCALATION in the tally."""
    t = run_demo("A", escalate=True)

    assert t.module == "A"
    assert t.escalate is True
    assert t["tally"]["escalation"] >= 1
    assert t["tally"]["topics"].get(Topic.ESCALATION, 0) >= 1

    # cyclone path drafts a public alert (multi-language)
    assert len(t["public_alert"]) >= 1
    langs = {a["language"] for a in t["public_alert"]}
    assert "en" in langs


# --------------------------------------------------------------- additional coverage
def test_all_modules_activate_and_run():
    for mod in DEMO_MODULES:
        t = run_demo(mod)
        assert t["activation"]["activated"] is True
        assert t["activation"]["decided"] == mod
        assert t["tally"]["message_count"] > 0
        # every run produces a report + brief
        assert t["report"]["markdown"]
        assert t["brief"]


def test_module_key_is_case_insensitive():
    lower = run_demo("b")
    upper = run_demo("B")
    assert lower.module == upper.module == "B"
    # deterministic: same dispatch/escalation tally across runs
    assert lower["tally"]["dispatch"] == upper["tally"]["dispatch"]
    assert lower["tally"]["escalation"] == upper["tally"]["escalation"]


def test_determinism_repeated_runs_match():
    a = run_demo("C", escalate=True)
    b = run_demo("C", escalate=True)
    assert a["tally"] == b["tally"]
    assert a["brief"] == b["brief"]
    assert a["report"]["dict"]["message_count"] == b["report"]["dict"]["message_count"]


def test_escalate_default_off_no_escalation_required():
    # Without escalate, the earthquake golden path still dispatches autonomously.
    t = run_demo("B", escalate=False)
    assert t.escalate is False
    assert t["tally"]["dispatch"] >= 1


def test_transcript_is_dict_like_and_jsonable():
    t = run_demo("B")
    # dict-like access
    assert "tally" in t
    assert t.get("missing", "x") == "x"
    assert set(["module", "tally", "report", "brief"]).issubset(set(t.keys()))
    # JSON-able
    blob = json.dumps(t.to_dict())
    assert json.loads(blob)["module"] == "B"


def test_to_markdown_renders_all_sections():
    md = run_demo("A", escalate=True).to_markdown()
    assert "# DisasterMind Demo" in md
    assert "## 1. Activation" in md
    assert "## 2. Pipeline" in md
    assert "## 3. After-Action Report" in md
    assert "## 4. Commander Situation Brief" in md
    assert "## 5. Public Alert" in md  # cyclone only


def test_unknown_module_raises():
    with pytest.raises(ValueError):
        run_demo_direct("Z")


def test_cli_main_prints_markdown(capsys):
    from disastermind.demo.__main__ import main

    rc = main(["B"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DisasterMind Demo" in out
    assert "After-Action Report" in out


def test_cli_main_json(capsys):
    from disastermind.demo.__main__ import main

    rc = main(["A", "--escalate", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["module"] == "A"
    assert data["tally"]["escalation"] >= 1
