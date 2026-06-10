"""Latency budgets & in-process readiness aggregation (PRD Step 10).

Two small, stdlib-only ops primitives that the reliability harness (and any
operator wiring) can opt into. They add NO behaviour at import time and change
no existing signatures — they are purely additive:

  * :class:`Timer` / :class:`LatencyBudget` — a context manager that measures how
    long a block took against an injectable clock (so tests are deterministic and
    never depend on wall-clock). A budget records whether the block fit inside a
    generous deadline; ``Timer`` is the budget-less variant. Neither raises on
    overrun (a missed budget is *reported*, never an exception — measuring the
    coordination step must never take the step down).

  * :class:`ReadinessAggregator` — a trivial in-process registry that folds many
    named readiness signals (callables or booleans) into one
    ``ready``/``not_ready`` verdict, mirroring the shape of
    :func:`disastermind.ops.health.readiness` so the two compose. A signal that
    raises is treated as *not ready* (and recorded) rather than propagating — an
    aggregator must never crash the probe it backs.

Everything here is deterministic: clocks are injectable, nothing sleeps, opens a
socket, or starts a thread. Inert by default.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

Clock = Callable[[], float]

#: A readiness signal is either a bool or a zero-arg callable returning bool-ish.
ReadinessSignal = Callable[[], Any]


# --------------------------------------------------------------------- latency
@dataclass
class Timer:
    """Context manager that measures the wall-time of a block (injectable clock).

    Usage::

        with Timer() as t:
            do_work()
        assert t.elapsed >= 0.0

    The ``clock`` defaults to :func:`time.perf_counter` but is injectable so a
    test can drive it from a manually-advanced fake clock and assert an exact
    ``elapsed`` with zero flakiness. Re-entering ``__enter__`` restarts the timer.
    Reading :attr:`elapsed` mid-block returns the time so far; after ``__exit__``
    it is frozen.
    """

    name: str = "block"
    clock: Clock = time.perf_counter

    started_at: float | None = field(default=None, init=False)
    stopped_at: float | None = field(default=None, init=False)

    def __enter__(self) -> "Timer":
        self.started_at = self.clock()
        self.stopped_at = None
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stopped_at = self.clock()
        return False  # never suppress an exception from the measured block

    @property
    def elapsed(self) -> float:
        """Seconds elapsed: final once stopped, live while running, 0 before start."""
        if self.started_at is None:
            return 0.0
        end = self.stopped_at if self.stopped_at is not None else self.clock()
        return max(0.0, end - self.started_at)

    @property
    def running(self) -> bool:
        return self.started_at is not None and self.stopped_at is None

    def snapshot(self) -> dict[str, Any]:
        """A JSON-friendly view of the timing (for /healthz / metrics)."""
        return {"name": self.name, "elapsed": self.elapsed, "running": self.running}


@dataclass
class LatencyBudget:
    """A :class:`Timer` with a deadline — did the block fit the budget?

    ``budget`` is a generous deadline in seconds (e.g. a coordination step budget).
    On exit, :attr:`within_budget` reports whether the measured ``elapsed`` stayed
    at or under ``budget`` and :attr:`overrun` is the (clamped) amount over. An
    overrun is **never** raised: a latency budget is an SLO you *observe*, not a
    guard that aborts the work — measuring the coordination loop must not take it
    down. Use :meth:`assert_within` explicitly if a test wants a hard check.

        with LatencyBudget(0.250, clock=fake) as b:
            step()
        assert b.within_budget
    """

    budget: float
    name: str = "budget"
    clock: Clock = time.perf_counter

    started_at: float | None = field(default=None, init=False)
    stopped_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.budget < 0:
            raise ValueError("budget must be >= 0")

    def __enter__(self) -> "LatencyBudget":
        self.started_at = self.clock()
        self.stopped_at = None
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stopped_at = self.clock()
        return False

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.stopped_at if self.stopped_at is not None else self.clock()
        return max(0.0, end - self.started_at)

    @property
    def within_budget(self) -> bool:
        """True iff the measured block stayed at or under the budget."""
        return self.elapsed <= self.budget

    @property
    def overrun(self) -> float:
        """Seconds over budget (0.0 when within budget)."""
        return max(0.0, self.elapsed - self.budget)

    def assert_within(self) -> None:
        """Raise :class:`BudgetExceeded` if the block overran (opt-in hard check)."""
        if not self.within_budget:
            raise BudgetExceeded(
                f"{self.name}: {self.elapsed:.6f}s exceeded budget {self.budget:.6f}s "
                f"by {self.overrun:.6f}s"
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "elapsed": self.elapsed,
            "budget": self.budget,
            "within_budget": self.within_budget,
            "overrun": self.overrun,
        }


class BudgetExceeded(RuntimeError):
    """Raised only by :meth:`LatencyBudget.assert_within` on an overrun."""


# ----------------------------------------------------------------- readiness agg
@dataclass
class ReadinessAggregator:
    """Fold many named readiness signals into one ``ready``/``not_ready`` verdict.

    A signal is registered by ``name`` and is either a literal ``bool`` or a
    zero-arg callable returning something truthy/falsy. :meth:`evaluate` runs them
    all and returns a dict shaped like :func:`disastermind.ops.health.readiness`::

        {"status": "ready"|"not_ready", "ready": bool,
         "checks": {name: "ok"|"fail", ...}}

    Robustness: a signal that *raises* is treated as ``fail`` (and the error
    recorded in the per-check detail) rather than propagating — an aggregator that
    backs a health probe must never itself crash the probe. With no signals the
    aggregator is *ready* by default (vacuously true); pass
    ``empty_is_ready=False`` to invert that.
    """

    empty_is_ready: bool = True
    _signals: list[tuple[str, ReadinessSignal]] = field(default_factory=list, init=False)

    def register(self, name: str, signal: ReadinessSignal) -> "ReadinessAggregator":
        """Register a readiness ``signal`` under ``name``. Returns self (chainable)."""
        self._signals.append((name, signal))
        return self

    @property
    def names(self) -> list[str]:
        """Registered signal names, in registration order."""
        return [n for n, _s in self._signals]

    @staticmethod
    def _resolve(signal: ReadinessSignal) -> tuple[bool, str | None]:
        """Resolve one signal to (ok, error_repr). Never raises."""
        try:
            value = signal() if callable(signal) else signal
            return bool(value), None
        except Exception as exc:  # noqa: BLE001 - a sick signal is "not ready", not a crash
            return False, repr(exc)

    def evaluate(self) -> dict[str, Any]:
        """Run every signal and return the aggregate readiness dict."""
        checks: dict[str, str] = {}
        details: dict[str, str] = {}
        for name, signal in self._signals:
            ok, err = self._resolve(signal)
            checks[name] = "ok" if ok else "fail"
            if err is not None:
                details[name] = err

        if not self._signals:
            ready = bool(self.empty_is_ready)
        else:
            ready = all(v == "ok" for v in checks.values())

        result: dict[str, Any] = {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "checks": checks,
        }
        if details:
            result["errors"] = details
        return result

    def is_ready(self) -> bool:
        """Convenience: just the boolean verdict."""
        return bool(self.evaluate()["ready"])


__all__ = [
    "Timer",
    "LatencyBudget",
    "BudgetExceeded",
    "ReadinessAggregator",
]
