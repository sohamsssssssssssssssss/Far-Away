"""Deterministic load / throughput benchmark harness (PRD Group A, Step 10).

This package drives *N* synthetic incidents through the full DisasterMind agent
DAG and collects throughput metrics **without any wall-clock measurement**, so it
runs in CI as a structural regression guard rather than a flaky benchmark.

  * :func:`~disastermind.benchmarks.harness.drive_n_incidents` wires the proven
    subscriber-before-producer DAG on a :class:`CountingBus` (an injected,
    monotone publish counter — never ``time.time``), seeds field teams, injects
    ``n`` synthetic A/B/C incidents and drives the
    :class:`~disastermind.orchestration.loop.CoordinationLoop` for ``cycles``
    cycles, returning a :class:`BenchmarkResult` of pure *counts*.
  * :func:`~disastermind.benchmarks.report.report` /
    :func:`~disastermind.benchmarks.report.to_markdown` normalise a result into a
    JSON-serialisable report dict and a deterministic Markdown table.

Standard-library only, offline, and deterministic by construction (PRD HARD
RULE 2): no network at import or in any test path, no timing in any metric, so
every reported number is a stable function of the message fan-out — not of the
host's speed.

Run a demo with ``python -m disastermind.benchmarks``.
"""
from __future__ import annotations

from .harness import (
    BenchmarkResult,
    CountingBus,
    drive_n_incidents,
)
from .report import report, to_markdown

__all__ = [
    "drive_n_incidents",
    "BenchmarkResult",
    "CountingBus",
    "report",
    "to_markdown",
]
