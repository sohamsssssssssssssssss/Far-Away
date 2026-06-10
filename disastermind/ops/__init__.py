"""Operational hardening for production deployment (PRD Step 10).

The ``ops`` package collects the cross-cutting concerns that turn the
DisasterMind coordination engine from a demo into something you can run on call:

  * :mod:`~disastermind.ops.health` — Kubernetes-style :func:`readiness` and
    :func:`liveness` probes over a built coordination loop.
  * :mod:`~disastermind.ops.retry` — a :func:`retry` decorator with exponential
    backoff (injectable sleep) and a :class:`CircuitBreaker` to wrap flaky
    external calls (feeds / dispatch) so a sick dependency fails fast instead of
    dragging the whole loop down.
  * :mod:`~disastermind.ops.shutdown` — a :class:`GracefulShutdown` handler that
    runs registered drain callbacks on SIGTERM/SIGINT so field teams keep their
    last orders.
  * :mod:`~disastermind.ops.config_check` — :func:`validate_settings` pre-flight
    configuration validation (offline / lexical).
  * :mod:`~disastermind.ops.budget` — a :class:`Timer` / :class:`LatencyBudget`
    context manager (injectable clock; never raises on overrun) and a
    :class:`ReadinessAggregator` that folds many named readiness signals into one
    verdict shaped like :func:`readiness`.

Everything here is stdlib-only, deterministic, and inert by default — importing
``disastermind.ops`` arms no signal handlers, opens no sockets, and starts no
threads. You opt in by calling these primitives where you need them.
"""
from __future__ import annotations

from .budget import (
    BudgetExceeded,
    LatencyBudget,
    ReadinessAggregator,
    Timer,
)
from .config_check import Issue, Severity, errors, is_valid, validate_settings
from .health import liveness, probe, readiness
from .retry import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
    backoff_schedule,
    circuit_breaker,
    retry,
)
from .shutdown import GracefulShutdown

__all__ = [
    # health
    "readiness",
    "liveness",
    "probe",
    # retry / circuit breaker
    "retry",
    "backoff_schedule",
    "CircuitBreaker",
    "BreakerState",
    "CircuitOpenError",
    "circuit_breaker",
    # shutdown
    "GracefulShutdown",
    # config validation
    "validate_settings",
    "Issue",
    "Severity",
    "errors",
    "is_valid",
    # latency budgets & readiness aggregation
    "Timer",
    "LatencyBudget",
    "BudgetExceeded",
    "ReadinessAggregator",
]
