"""Scenario-simulator + CLI tests (PRD Group A, Step 10).

Each synthetic scenario (A=cyclone/flood, B=earthquake, C=urban fire/collapse)
must drive the wired agent DAG all the way to a ``Topic.DISPATCH`` (or, in the
escalation variant, a ``Topic.ESCALATION``). The CLI (``python -m disastermind``)
must expose ``run`` / ``simulate`` / ``verify-audit`` and behave correctly,
including detecting a tampered audit chain.

Stdlib-only, offline, deterministic (PRD HARD RULE 2 / Step 10).
"""
from __future__ import annotations

import io
import json

import pytest

from disastermind import scenarios
from disastermind.cli import build_parser, main
from disastermind.core.contracts import (
    EscalationTrigger,
    MessageType,
    Module,
    Topic,
)


# --------------------------------------------------------------------------- A/B/C
@pytest.mark.parametrize("key", ["A", "B", "C"])
def test_each_module_reaches_dispatch_or_escalation(key: str) -> None:
    """Every module's default scenario lands at a DISPATCH or ESCALATION."""
    result = scenarios.run_scenario(key)
    assert result.succeeded, f"module {key} reached neither DISPATCH nor ESCALATION"
    # The default (non-escalating) scenarios autonomously dispatch.
    assert result.reached_dispatch, f"module {key} produced no real DISPATCH"


@pytest.mark.parametrize("key", ["A", "B", "C"])
def test_each_module_exercises_the_load_bearing_chain(key: str) -> None:
    """RAW_FEED must flow through prediction/resource/routing/field to dispatch."""
    result = scenarios.run_scenario(key)
    counts = result.topic_counts
    assert counts.get(Topic.RAW_FEED, 0) > 0, f"{key}: no RAW_FEED"
    assert counts.get(Topic.PREDICTION, 0) > 0, f"{key}: no PREDICTION"
    assert counts.get(Topic.RESOURCE_PLAN, 0) > 0, f"{key}: no RESOURCE_PLAN"
    assert counts.get(Topic.ROUTING_PLAN, 0) > 0, f"{key}: no ROUTING_PLAN (dead edge)"
    assert counts.get(Topic.FIELD_ORDER, 0) > 0, f"{key}: no FIELD_ORDER"
    assert counts.get(Topic.DISPATCH, 0) > 0, f"{key}: no DISPATCH"


def test_module_messages_carry_the_right_module() -> None:
    """A scenario's dispatches are tagged with its own module (no cross-talk)."""
    for key, module in (
        ("A", Module.CYCLONE_FLOOD),
        ("B", Module.EARTHQUAKE),
        ("C", Module.FIRE_COLLAPSE),
    ):
        result = scenarios.run_scenario(key)
        assert any(d.module is module for d in result.dispatches), (
            f"{key}: no DISPATCH tagged {module}"
        )


def test_returns_driven_coordination_loop() -> None:
    """Generators return the driven CoordinationLoop with a populated bus."""
    from disastermind.orchestration.loop import CoordinationLoop

    loop = scenarios.simulate_earthquake()
    assert isinstance(loop, CoordinationLoop)
    assert loop.bus.history, "driven loop has an empty bus history"
    assert loop.cycle >= 1, "loop was not driven for at least one cycle"


# ----------------------------------------------------------------- escalation
@pytest.mark.parametrize(
    "key, trigger",
    [
        ("A", EscalationTrigger.MASS_EVACUATION),
        ("B", EscalationTrigger.CROSS_STATE_RESOURCE),
        ("C", EscalationTrigger.REQUISITION_PRIVATE),
    ],
)
def test_escalation_variant_produces_escalation(key: str, trigger: EscalationTrigger) -> None:
    """``escalate=True`` makes the Commander publish a matching ESCALATION."""
    result = scenarios.run_scenario(key, escalate=True)
    assert result.reached_escalation, f"{key} escalate=True produced no ESCALATION"
    esc = result.escalations[-1]
    assert esc.type is MessageType.ESCALATION
    assert esc.escalation_trigger is trigger


# --------------------------------------------------------------------------- CLI
def _run_cli(argv: list[str]) -> tuple[int, str]:
    """Invoke the CLI capturing stdout (argparse writes its own to the parser)."""
    buf = io.StringIO()
    parser = build_parser()
    args = parser.parse_args(argv)
    code = int(args.func(args, out=buf))
    return code, buf.getvalue()


@pytest.mark.parametrize("key", ["A", "B", "C"])
def test_cli_simulate_each_module(key: str) -> None:
    code, text = _run_cli(["simulate", key])
    assert code == 0
    assert "DISPATCH orders:" in text
    assert "topic counts:" in text
    assert Topic.DISPATCH in text


def test_cli_simulate_escalate_prints_escalation() -> None:
    code, text = _run_cli(["simulate", "B", "--escalate"])
    assert code == 0
    assert "ESCALATION" in text
    assert EscalationTrigger.CROSS_STATE_RESOURCE.value in text


def test_cli_simulate_accepts_lowercase_module() -> None:
    code, text = _run_cli(["simulate", "a"])
    assert code == 0
    assert "Cyclone / Flood" in text


def test_cli_run_executes_cycles(tmp_path) -> None:
    audit = tmp_path / "run-audit.jsonl"
    code, text = _run_cli(["run", "--max-cycles", "2", "--audit", str(audit)])
    assert code == 0
    assert "2 cycle(s) executed" in text
    assert audit.exists()
    # Something must have been logged through the running DAG.
    assert audit.read_text().strip(), "run produced an empty audit log"


def test_cli_run_no_audit_writes_nothing(tmp_path) -> None:
    code, text = _run_cli(["run", "--max-cycles", "1", "--no-audit"])
    assert code == 0
    assert "cycle(s) executed" in text


def test_cli_verify_audit_ok_then_tampered(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    # Produce a real on-disk hash-chain via a run.
    rc, _ = _run_cli(["run", "--max-cycles", "1", "--audit", str(audit)])
    assert rc == 0

    code, text = _run_cli(["verify-audit", str(audit)])
    assert code == 0, "clean audit chain did not verify"
    assert "OK" in text

    # Tamper with a middle record's body without recomputing its hash.
    lines = [ln for ln in audit.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 2
    idx = len(lines) // 2
    rec = json.loads(lines[idx])
    rec["reasoning"] = list(rec.get("reasoning", [])) + ["TAMPERED"]
    lines[idx] = json.dumps(rec, separators=(",", ":"))
    audit.write_text("\n".join(lines) + "\n")

    code, text = _run_cli(["verify-audit", str(audit)])
    assert code == 1, "tampered audit chain still verified"
    assert "TAMPERED" in text


def test_cli_verify_audit_missing_file(tmp_path) -> None:
    code, _ = _run_cli(["verify-audit", str(tmp_path / "nope.jsonl")])
    assert code == 2


def test_cli_main_no_command_prints_help() -> None:
    # No subcommand -> prints help, exit 0 (does not raise SystemExit).
    code = main([])
    assert code == 0


def test_cli_simulate_unknown_module_rejected() -> None:
    # argparse rejects an out-of-choice module with SystemExit(2).
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["simulate", "Z"])
    assert exc.value.code == 2
