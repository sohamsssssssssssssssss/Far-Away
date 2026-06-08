"""Structural invariants for the deterministic load/throughput harness (Step 10).

This file guards the :mod:`disastermind.benchmarks` harness as a CI regression
check. Every assertion is about *structure* — counts, monotonicity, boundedness,
serialisability — and never about latency or wall-clock time, so nothing here can
flake on a slow or busy host (PRD HARD RULE 2). The metrics the harness returns
are pure functions of the synchronous in-memory message fan-out, so K incidents
either flow through ``RAW_FEED -> ... -> DISPATCH`` or they don't.
"""
from __future__ import annotations

import json

import pytest

from disastermind.benchmarks import (
    BenchmarkResult,
    CountingBus,
    drive_n_incidents,
    report,
    to_markdown,
)
from disastermind.core.bus import InMemoryBus
from disastermind.core.contracts import Topic


# --------------------------------------------------------------------- driver
def test_k_incidents_run_without_exception() -> None:
    """A batch of incidents drives the DAG cleanly and returns a result."""
    result = drive_n_incidents(9, cycles=2)
    assert isinstance(result, BenchmarkResult)
    assert result.incidents == 9
    assert result.cycles == 2
    assert result.modules == ["A", "B", "C"]


def test_dispatches_are_produced() -> None:
    """Injected incidents flow all the way to real DISPATCH orders (> 0)."""
    result = drive_n_incidents(9, cycles=2)
    assert result.dispatches > 0, "no DISPATCH orders produced by the driven DAG"


def test_messages_are_processed() -> None:
    """The injected publish counter advances (work was done)."""
    result = drive_n_incidents(6, cycles=1)
    assert result.messages_processed > 0
    # Every message recorded in history was published through the bus.
    assert result.bus_history_len <= result.messages_processed


def test_load_bearing_chain_is_exercised() -> None:
    """RAW_FEED -> PREDICTION -> RESOURCE -> ROUTING -> FIELD -> DISPATCH fires."""
    result = drive_n_incidents(6, cycles=1)
    counts = result.topic_counts
    for topic in (
        Topic.RAW_FEED,
        Topic.PREDICTION,
        Topic.RESOURCE_PLAN,
        Topic.ROUTING_PLAN,
        Topic.FIELD_ORDER,
        Topic.DISPATCH,
    ):
        assert counts.get(topic, 0) > 0, f"dead edge: no traffic on {topic}"


# ------------------------------------------------------------------- scaling
def test_messages_grow_with_cycles() -> None:
    """More cycles strictly process more messages (deterministic, count-based)."""
    one = drive_n_incidents(6, cycles=1)
    three = drive_n_incidents(6, cycles=3)
    assert three.cycles == 3 and one.cycles == 1
    assert three.messages_processed > one.messages_processed


def test_per_cycle_messages_length_matches_cycles() -> None:
    """One per-cycle delta is recorded per driven cycle and each is non-negative."""
    result = drive_n_incidents(6, cycles=4)
    assert len(result.per_cycle_messages) == 4
    assert all(delta >= 0 for delta in result.per_cycle_messages)
    # The per-cycle deltas account for every message published while driving.
    assert sum(result.per_cycle_messages) <= result.messages_processed


def test_more_incidents_do_not_reduce_dispatches() -> None:
    """Scaling up the incident count is monotone in dispatch volume."""
    small = drive_n_incidents(3, cycles=1)
    large = drive_n_incidents(12, cycles=1)
    assert large.dispatches >= small.dispatches


# ------------------------------------------------------------------ bounded
def test_history_is_bounded_by_cap() -> None:
    """The bus ring buffer never exceeds its configured cap (resource safety)."""
    result = drive_n_incidents(12, cycles=3, history_cap=64)
    assert result.bus_history_cap == 64
    assert result.bus_history_len <= 64


def test_large_run_stays_within_cap() -> None:
    """A heavier soak run still respects the bounded history (no unbounded growth)."""
    result = drive_n_incidents(30, cycles=4, history_cap=100)
    assert result.bus_history_len <= result.bus_history_cap == 100
    # Even bounded, the run still reached dispatches.
    assert result.dispatches > 0


# ------------------------------------------------------------------- module
def test_single_module_selection() -> None:
    """A single-module run only round-robins that module."""
    result = drive_n_incidents(4, cycles=1, modules=["B"])
    assert result.modules == ["B"]
    assert result.dispatches > 0


def test_unknown_module_falls_back() -> None:
    """An unknown module string falls back to a safe default rather than erroring."""
    result = drive_n_incidents(3, cycles=1, modules=["Z", "?"])
    assert result.modules  # non-empty fallback
    assert all(m in {"A", "B", "C"} for m in result.modules)


# --------------------------------------------------------------------- edges
def test_zero_incidents_is_safe() -> None:
    """Driving with no incidents does not raise and still returns a result."""
    result = drive_n_incidents(0, cycles=1)
    assert result.incidents == 0
    assert result.messages_processed >= 0
    assert result.bus_history_len <= result.bus_history_cap


def test_negative_inputs_are_clamped() -> None:
    """Negative incident / cycle counts clamp to safe minimums (never crash)."""
    result = drive_n_incidents(-5, cycles=-2)
    assert result.incidents == 0
    assert result.cycles >= 1


# ------------------------------------------------------------------ counting
def test_counting_bus_counts_publishes() -> None:
    """CountingBus increments its publish counter and mirrors history."""
    bus = CountingBus(inner=InMemoryBus(history=10))
    assert bus.published == 0
    from disastermind.scenarios.base import seed_field_teams

    seed_field_teams(bus)
    assert bus.published == 1
    assert len(bus.history) == 1
    bus.mark_cycle()
    assert bus.cycle_marks == [1]


# ------------------------------------------------------------------- reports
def test_result_is_json_serialisable() -> None:
    """BenchmarkResult.to_dict() round-trips through JSON (CI snapshot friendly)."""
    result = drive_n_incidents(6, cycles=2)
    d = result.to_dict()
    encoded = json.dumps(d)  # must not raise
    assert json.loads(encoded) == d


def test_report_derives_count_ratios() -> None:
    """report() adds derived *count ratios* (never per-second throughput)."""
    result = drive_n_incidents(6, cycles=2)
    rep = report(result)
    der = rep["derived"]
    assert "messages_per_incident" in der
    assert "dispatches_per_incident" in der
    assert "mean_messages_per_cycle" in der
    assert "history_at_cap" in der
    # No timing keys leaked into the report.
    flat = json.dumps(rep).lower()
    assert "per_second" not in flat and "latency" not in flat and "seconds" not in flat


def test_report_accepts_plain_mapping() -> None:
    """report() works on a re-loaded metrics dict, not just a BenchmarkResult."""
    result = drive_n_incidents(4, cycles=1)
    from_obj = report(result)
    from_dict = report(result.to_dict())
    assert from_obj == from_dict


def test_report_marks_history_at_cap() -> None:
    """history_at_cap is True exactly when the ring buffer filled to its cap."""
    capped = report(drive_n_incidents(20, cycles=3, history_cap=40))
    roomy = report(drive_n_incidents(1, cycles=1, history_cap=5000))
    assert capped["derived"]["history_at_cap"] is True
    assert roomy["derived"]["history_at_cap"] is False


def test_to_markdown_is_deterministic_and_structured() -> None:
    """to_markdown() renders a stable table; identical inputs -> identical text."""
    result = drive_n_incidents(6, cycles=2)
    md1 = to_markdown(result)
    md2 = to_markdown(result)
    assert md1 == md2
    assert md1.startswith("# DisasterMind throughput benchmark")
    assert "| metric | value |" in md1
    assert "messages_processed" in md1
    assert "## topic counts" in md1


# --------------------------------------------------------------------- __main__
def test_module_main_runs_demo() -> None:
    """`python -m disastermind.benchmarks` entry point runs and emits Markdown."""
    import io

    from disastermind.benchmarks.__main__ import main as bench_main

    buf = io.StringIO()
    code = bench_main(["-n", "3", "-c", "1"], out=buf)
    assert code == 0
    assert "DisasterMind throughput benchmark" in buf.getvalue()


def test_module_main_json_output() -> None:
    """The --json flag emits a parseable normalised report."""
    import io

    from disastermind.benchmarks.__main__ import main as bench_main

    buf = io.StringIO()
    code = bench_main(["-n", "3", "-c", "1", "--json"], out=buf)
    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["incidents"] == 3
    assert "derived" in payload
