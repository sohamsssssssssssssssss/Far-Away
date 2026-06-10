"""disastermind.demo — a narrated, offline, deterministic golden-path runner.

This package demonstrates the *whole* DisasterMind pipeline end to end without a
network, heavy dependency, or wall-clock dependence (PRD Step 10 graceful
degradation). :func:`run_demo` ties together the four stable, already-built
subsystems —

  * :mod:`disastermind.orchestration.triggers` (activation predicates),
  * :mod:`disastermind.scenarios`             (driven CoordinationLoop),
  * :mod:`disastermind.reporting`             (after-action report), and
  * :mod:`disastermind.llm`                   (situation brief + public alert) —

into a single :class:`~disastermind.demo.runner.DemoTranscript` that reads like a
story: activation -> pipeline tally -> report -> commander brief (-> public alert
for a cyclone). The transcript is a plain ``dict`` (JSON-able) and also renders
to Markdown for the CLI (``python -m disastermind.demo B``).

The runner imports ONLY the four stable subsystems above plus
:mod:`disastermind.core.contracts`; it never touches diagnostics, benchmarks, or
prediction internals.
"""
from __future__ import annotations

from .runner import (
    DEMO_MODULES,
    DemoTranscript,
    run_demo,
)

__all__ = [
    "run_demo",
    "DemoTranscript",
    "DEMO_MODULES",
]
