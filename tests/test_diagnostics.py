"""Tests for the diagnostics ("doctor") self-check package.

Offline, stdlib-only, deterministic (PRD HARD RULE 2). We assert that:

  * a healthy system reports OK with a balanced topic DAG and exit code 0;
  * every wired module imports + constructs at least one agent;
  * a *synthetic* DAG imbalance (orphan producer / dead subscriber) is detected;
  * config sanity catches a non-positive interval / missing DSN;
  * the audit-chain probe verifies a good chain and flags a tampered one;
  * the optional backend probe never hard-fails (WARN/SKIP only);
  * ``python -m disastermind.diagnostics`` works and renders dict + Markdown.
"""
from __future__ import annotations

import json

import pytest

from disastermind.diagnostics import (
    Report,
    Status,
    analyse_dag,
    known_contract_topics,
    produced_topics,
    run_diagnostics,
    subscribed_topics,
)
from disastermind.diagnostics import checks as dchecks
from disastermind.diagnostics.report import Check


# --------------------------------------------------------------- healthy system
def test_healthy_system_reports_ok_and_balanced_dag() -> None:
    report = run_diagnostics()
    assert isinstance(report, Report)
    # No probe FAILED on a clean wired system.
    assert report.failures() == [], [c.detail for c in report.failures()]
    assert report.ok is True
    assert report.exit_code == 0
    assert report.status in (Status.OK, Status.SKIP)

    # The DAG balance probes must both be present and OK.
    by_name = {c.name: c for c in report.checks}
    assert by_name["dag.orphan_producers"].status is Status.OK
    assert by_name["dag.dead_subscribers"].status is Status.OK


def test_every_module_imports_and_constructs_agents() -> None:
    report = run_diagnostics()
    from disastermind.orchestration.build import MODULE_BUILD_PATHS

    # Each MODULE_BUILD_PATHS entry produced a module.build:<path> check.
    build_checks = [c for c in report.checks if c.name.startswith("module.build:")]
    assert len(build_checks) == len(MODULE_BUILD_PATHS)
    assert all(c.status is Status.OK for c in build_checks), [
        (c.name, c.detail) for c in build_checks if c.status is not Status.OK
    ]
    assert report.meta["agents_constructed"] > 0
    assert report.meta["modules_degraded"] == []


def test_dag_introspection_against_real_build() -> None:
    """The produced/subscribed maps come straight off a wired InMemoryBus."""
    from disastermind.orchestration.build import build_system

    loop = build_system()
    subscribed = subscribed_topics(loop.bus)
    # The well-known contract topics are all subscribed in a full wiring...
    contract = known_contract_topics()
    # ...with at most the narrative sink and the review topic differing.
    missing = contract - subscribed
    assert missing <= {"tier1.escalation_narrative"}, missing
    # Before any run nothing has been produced.
    assert produced_topics(loop.bus) == set()


# ----------------------------------------------------------- synthetic imbalance
def test_synthetic_orphan_producer_is_detected() -> None:
    """A produced topic with no subscriber (and not a sink) is an imbalance."""
    analysis = analyse_dag(
        produced={"tier2.prediction", "ghost.topic.no_listener"},
        subscribed={"tier2.prediction"},
    )
    assert analysis["orphan_producers"] == ["ghost.topic.no_listener"]
    assert analysis["dead_subscribers"] == []


def test_synthetic_dead_subscriber_is_detected() -> None:
    """A subscriber waiting on a topic nobody produces (nor declares) is bad."""
    analysis = analyse_dag(
        produced={"tier2.prediction"},
        subscribed={"tier2.prediction", "phantom.topic.never_emitted"},
    )
    assert analysis["dead_subscribers"] == ["phantom.topic.never_emitted"]
    assert analysis["orphan_producers"] == []


def test_terminal_sink_is_not_an_orphan() -> None:
    """The escalation narrative is a by-design terminal sink, not an orphan."""
    assert dchecks._is_terminal_sink("tier1.escalation_narrative")
    analysis = analyse_dag(produced={"tier1.escalation_narrative"}, subscribed=set())
    assert analysis["orphan_producers"] == []


def test_known_contract_topic_subscriber_is_not_dead() -> None:
    """A subscriber on a declared Topic constant is fine even if unproduced here."""
    contract = known_contract_topics()
    assert "tier1.commander_review" in contract
    analysis = analyse_dag(produced=set(), subscribed={"tier1.commander_review"})
    assert analysis["dead_subscribers"] == []


def test_check_dag_reports_fail_on_injected_imbalance(monkeypatch) -> None:
    """End-to-end: an injected imbalance turns the DAG probe into a FAIL.

    We monkeypatch the produced/subscribed introspection so ``check_dag`` sees a
    synthetic orphan producer AND a dead subscriber, without touching the frozen
    agent code.
    """
    monkeypatch.setattr(
        dchecks, "produced_topics", lambda bus: {"tier2.prediction", "orphan.out"}
    )
    monkeypatch.setattr(
        dchecks, "subscribed_topics", lambda bus: {"tier2.prediction", "dead.in"}
    )

    report = Report()
    dchecks.check_dag(report, {"settings": None})
    by_name = {c.name: c for c in report.checks}
    assert by_name["dag.orphan_producers"].status is Status.FAIL
    assert "orphan.out" in by_name["dag.orphan_producers"].detail
    assert by_name["dag.dead_subscribers"].status is Status.FAIL
    assert "dead.in" in by_name["dag.dead_subscribers"].detail
    assert report.exit_code == 1


# --------------------------------------------------------------------- config
def test_config_sanity_flags_bad_settings() -> None:
    class BadSettings:
        loop_interval_seconds = 0          # invalid
        escalation_timeout_seconds = -1    # invalid
        grid_cell_meters = 100
        postgres_dsn = "postgresql://localhost/x"
        timescale_dsn = ""                 # missing
        audit_log_path = ""

    report = Report()
    dchecks.check_config(report, BadSettings())
    by_name = {c.name: c for c in report.checks}
    assert by_name["config.loop_interval"].status is Status.FAIL
    assert by_name["config.escalation_timeout"].status is Status.FAIL
    assert by_name["config.dsn.timescale"].status is Status.FAIL
    assert by_name["config.dsn.postgres"].status is Status.OK


def test_config_sanity_passes_default_settings() -> None:
    from disastermind.core.config import Settings

    report = Report()
    dchecks.check_config(report, Settings())
    assert report.failures() == []


# ---------------------------------------------------------------------- audit
def test_audit_chain_verifies_good_and_detects_tamper(tmp_path) -> None:
    from disastermind.audit.decision_log import DecisionLogger
    from disastermind.core.contracts import Message, MessageType, Priority

    path = tmp_path / "audit.jsonl"
    logger = DecisionLogger(path=str(path))
    for i in range(3):
        logger.record(
            Message(
                sender="a",
                recipient="b",
                type=MessageType.ALERT,
                priority=Priority.HIGH,
                payload={"i": i},
            )
        )

    # Good chain -> OK via run_diagnostics(audit_path=...).
    report = run_diagnostics(audit_path=str(path))
    audit_chk = next(c for c in report.checks if c.name == "audit.chain")
    assert audit_chk.status is Status.OK

    # Tamper a middle line, then re-run -> FAIL + exit code 1.
    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["payload"] = {"i": 999}
    lines[1] = json.dumps(rec, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    report2 = run_diagnostics(audit_path=str(path))
    audit_chk2 = next(c for c in report2.checks if c.name == "audit.chain")
    assert audit_chk2.status is Status.FAIL
    assert report2.exit_code == 1


def test_audit_skipped_when_no_path() -> None:
    report = Report()
    dchecks.check_audit(report, None)
    chk = report.checks[0]
    assert chk.name == "audit.chain"
    assert chk.status is Status.SKIP


def test_audit_warns_on_missing_file(tmp_path) -> None:
    report = Report()
    dchecks.check_audit(report, str(tmp_path / "does-not-exist.jsonl"))
    assert report.checks[0].status is Status.WARN


# ------------------------------------------------------------------- backends
def test_backend_probe_skips_when_health_absent(monkeypatch) -> None:
    """When integrations.health is missing, the backend probe SKIPs (never FAIL)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "disastermind.integrations.health":
            raise ImportError("simulated absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    report = Report()
    dchecks.check_backends(report, object())
    chk = next(c for c in report.checks if c.name == "backends.reachability")
    assert chk.status is Status.SKIP


def test_backend_probe_maps_unreachable_to_warn() -> None:
    """An unreachable backend is a WARN, never a FAIL (graceful degradation)."""
    report = Report()
    dchecks._record_backend_results(
        report, {"postgres": True, "kafka": False, "elasticsearch": {"ok": False}}
    )
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["backend.postgres"] is Status.OK
    assert statuses["backend.kafka"] is Status.WARN
    assert statuses["backend.elasticsearch"] is Status.WARN
    assert report.failures() == []


def test_backend_probe_never_fails_overall() -> None:
    """Even with everything down, run_diagnostics keeps backends non-fatal."""
    report = run_diagnostics()
    backend_checks = [c for c in report.checks if c.name.startswith("backend")]
    assert all(c.status is not Status.FAIL for c in backend_checks)


# -------------------------------------------------------------------- renderers
def test_report_renders_dict_markdown_and_exit_code() -> None:
    report = run_diagnostics()
    d = report.to_dict()
    assert set(d) >= {"status", "ok", "exit_code", "counts", "checks", "meta"}
    assert isinstance(d["checks"], list) and d["checks"]
    assert d["exit_code"] in (0, 1)

    md = report.to_markdown()
    assert md.startswith("# DisasterMind doctor")
    assert "check" in md and "detail" in md

    # JSON round-trips.
    parsed = json.loads(report.to_json())
    assert parsed["exit_code"] == report.exit_code


def test_report_worst_status_rollup() -> None:
    r = Report()
    r.add("a", Status.OK)
    assert r.status is Status.OK and r.exit_code == 0
    r.add("b", Status.WARN)
    assert r.status is Status.WARN and r.exit_code == 0  # warn does not fail
    r.add("c", Status.FAIL)
    assert r.status is Status.FAIL and r.exit_code == 1
    assert r.healthy is False


def test_check_helpers_roundtrip() -> None:
    chk = Check(name="x", status=Status.OK, detail="hi", data={"k": 1})
    assert chk.ok is True
    assert chk.to_dict() == {
        "name": "x",
        "status": "ok",
        "detail": "hi",
        "data": {"k": 1},
    }


# -------------------------------------------------------------------- __main__
def test_main_entrypoint_returns_zero_on_healthy_system(capsys) -> None:
    from disastermind.diagnostics.__main__ import main

    code = main([])
    out = capsys.readouterr().out
    assert code == 0
    assert "DisasterMind doctor" in out


def test_main_entrypoint_json_flag(capsys) -> None:
    from disastermind.diagnostics.__main__ import main

    code = main(["--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["exit_code"] == code


def test_main_entrypoint_detects_tampered_audit(tmp_path, capsys) -> None:
    from disastermind.audit.decision_log import DecisionLogger
    from disastermind.core.contracts import Message, MessageType, Priority
    from disastermind.diagnostics.__main__ import main

    path = tmp_path / "audit.jsonl"
    logger = DecisionLogger(path=str(path))
    logger.record(
        Message(sender="a", recipient="b", type=MessageType.ALERT, priority=Priority.HIGH)
    )
    logger.record(
        Message(sender="a", recipient="b", type=MessageType.ALERT, priority=Priority.HIGH)
    )
    # Corrupt the first record's stored hash.
    lines = path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["_hash"] = "deadbeef" * 8
    lines[0] = json.dumps(rec, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    code = main(["--audit-path", str(path)])
    assert code == 1
