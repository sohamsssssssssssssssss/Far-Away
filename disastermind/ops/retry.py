"""Resilient call wrappers for flaky external dependencies (PRD Step 10).

DisasterMind talks to the outside world for *feeds* (USGS / IMD / FIRMS / Open-Meteo)
and *dispatch* (Twilio / FCM / Iridium). Those calls are the parts most likely to
flap during a real disaster — congested networks, rate limits, partial outages.
This module gives two stdlib-only, fully-deterministic primitives to wrap them:

  * :func:`retry` — a decorator that retries a callable a bounded number of times
    with **exponential backoff** (and optional jitter). The ``sleep`` function is
    *injectable* so tests never actually sleep, and the backoff schedule is
    deterministic (and inspectable via :func:`backoff_schedule`).

  * :class:`CircuitBreaker` — the classic three-state breaker (``closed`` →
    ``open`` → ``half_open``). It opens after ``failure_threshold`` consecutive
    failures, rejects calls fast while open, then after a ``reset_timeout``
    cooldown moves to ``half_open`` to test the waters with a single trial call.
    The clock is injectable so the cooldown is testable without wall-clock.

Both are inert by default — nothing imports or schedules anything at module load,
so the existing suite is unaffected. Wrap a call only where you opt in.
"""
from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable


# --------------------------------------------------------------------------- retry
def backoff_schedule(
    attempts: int,
    *,
    base_delay: float = 0.5,
    factor: float = 2.0,
    max_delay: float | None = None,
) -> list[float]:
    """Return the deterministic backoff delays *between* ``attempts`` tries.

    For ``attempts`` total tries there are ``attempts - 1`` waits. Delay *i*
    (0-indexed) is ``base_delay * factor**i`` clamped to ``max_delay``.

        backoff_schedule(4)            -> [0.5, 1.0, 2.0]
        backoff_schedule(4, max_delay=1.0) -> [0.5, 1.0, 1.0]
    """
    if attempts <= 1:
        return []
    delays: list[float] = []
    delay = float(base_delay)
    for _ in range(attempts - 1):
        d = delay if max_delay is None else min(delay, float(max_delay))
        delays.append(d)
        delay *= factor
    return delays


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    factor: float = 2.0,
    max_delay: float | None = None,
    exceptions: type[BaseException] | tuple[type[BaseException], ...] = Exception,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[float], float] | None = None,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
):
    """Decorator: retry the wrapped callable with exponential backoff.

    Parameters
    ----------
    attempts:
        Total number of tries (>=1). ``attempts=1`` means "no retry".
    base_delay, factor, max_delay:
        Exponential schedule (see :func:`backoff_schedule`).
    exceptions:
        Only these exception types trigger a retry; anything else propagates
        immediately (a programming error should not be retried).
    sleep:
        Injected sleep function — defaults to :func:`time.sleep`. **Tests pass a
        no-op (or recorder) so no real sleeping happens.**
    jitter:
        Optional ``delay -> delay`` transform applied to each computed delay.
        Omit (the default) for fully deterministic timing.
    on_retry:
        Optional ``(attempt_index, exc, delay)`` hook invoked before each sleep
        (useful for logging/metrics). ``attempt_index`` is 1-based.

    The last exception is re-raised once ``attempts`` is exhausted.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    _sleep = sleep if sleep is not None else time.sleep
    delays = backoff_schedule(
        attempts, base_delay=base_delay, factor=factor, max_delay=max_delay
    )

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last: BaseException | None = None
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # type: ignore[misc]
                    last = exc
                    if i >= attempts - 1:
                        break  # no wait after the final attempt
                    delay = delays[i]
                    if jitter is not None:
                        delay = jitter(delay)
                    if on_retry is not None:
                        on_retry(i + 1, exc, delay)
                    _sleep(delay)
            assert last is not None  # at least one attempt always ran
            raise last

        return wrapper

    return decorator


# ------------------------------------------------------------------- circuit breaker
class BreakerState(str, Enum):
    """The three states of a :class:`CircuitBreaker`."""

    CLOSED = "closed"      # healthy: calls flow through
    OPEN = "open"          # tripped: calls rejected fast until cooldown elapses
    HALF_OPEN = "half_open"  # probing: a single trial call is allowed through


class CircuitOpenError(RuntimeError):
    """Raised when a call is attempted while the breaker is OPEN (fast-fail)."""


@dataclass
class CircuitBreaker:
    """A closed/open/half-open circuit breaker for a flaky external call.

    Lifecycle
    ---------
    * **closed** — calls pass through. Each failure increments a counter; once it
      reaches ``failure_threshold`` consecutive failures the breaker trips to
      **open**. Any success resets the counter.
    * **open** — calls are rejected immediately with :class:`CircuitOpenError`
      (fast-fail, no load on the sick dependency). After ``reset_timeout`` seconds
      have elapsed since it opened, the next call is allowed and the breaker moves
      to **half_open**.
    * **half_open** — exactly one trial call is permitted. If it succeeds (or
      ``success_threshold`` successes complete) the breaker closes; if it fails the
      breaker re-opens and the cooldown restarts.

    The ``clock`` is injectable (defaults to :func:`time.monotonic`) so the
    cooldown is fully testable without sleeping.
    """

    failure_threshold: int = 5
    reset_timeout: float = 30.0
    success_threshold: int = 1
    exceptions: type[BaseException] | tuple[type[BaseException], ...] = Exception
    clock: Callable[[], float] = time.monotonic

    state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    success_count: int = field(default=0, init=False)
    opened_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")

    # ------------------------------------------------------------------ queries
    def _cooldown_elapsed(self) -> bool:
        if self.opened_at is None:
            return True
        return (self.clock() - self.opened_at) >= self.reset_timeout

    def allows_request(self) -> bool:
        """True iff a call would currently be permitted (advances open→half_open).

        Calling this while OPEN and past the cooldown transitions the breaker to
        HALF_OPEN (so the next call is treated as the trial). This is also the
        gate :meth:`call` uses internally.
        """
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.HALF_OPEN:
            return True
        # OPEN: permit one probe once the cooldown has elapsed.
        if self._cooldown_elapsed():
            self.state = BreakerState.HALF_OPEN
            self.success_count = 0
            return True
        return False

    @property
    def is_open(self) -> bool:
        return self.state is BreakerState.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state is BreakerState.CLOSED

    @property
    def is_half_open(self) -> bool:
        return self.state is BreakerState.HALF_OPEN

    # ----------------------------------------------------------- state transitions
    def record_success(self) -> None:
        """Register a successful call and advance the state machine."""
        if self.state is BreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self._close()
        else:
            self._close()

    def record_failure(self) -> None:
        """Register a failed call and advance the state machine."""
        if self.state is BreakerState.HALF_OPEN:
            # A failure during probing re-opens immediately.
            self._open()
            return
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self.state = BreakerState.OPEN
        self.opened_at = self.clock()
        self.success_count = 0

    def _close(self) -> None:
        self.state = BreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.opened_at = None

    def reset(self) -> None:
        """Force the breaker back to a clean CLOSED state."""
        self._close()

    # --------------------------------------------------------------------- call
    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Invoke ``fn`` through the breaker.

        Raises :class:`CircuitOpenError` immediately if the breaker is open and
        the cooldown has not elapsed; otherwise runs ``fn`` and records the
        outcome, re-raising any error from ``fn`` after updating state.
        """
        if not self.allows_request():
            raise CircuitOpenError(
                f"circuit breaker is OPEN (cooldown {self.reset_timeout}s not elapsed)"
            )
        try:
            result = fn(*args, **kwargs)
        except self.exceptions:  # type: ignore[misc]
            self.record_failure()
            raise
        else:
            self.record_success()
            return result

    def __call__(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Use the breaker as a decorator wrapping ``fn``."""

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return self.call(fn, *args, **kwargs)

        return wrapper

    def snapshot(self) -> dict[str, Any]:
        """A JSON-friendly view of the breaker's current state (for /healthz)."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "reset_timeout": self.reset_timeout,
            "opened_at": self.opened_at,
        }


def circuit_breaker(
    *,
    failure_threshold: int = 5,
    reset_timeout: float = 30.0,
    success_threshold: int = 1,
    exceptions: type[BaseException] | tuple[type[BaseException], ...] = Exception,
    clock: Callable[[], float] = time.monotonic,
) -> CircuitBreaker:
    """Construct a :class:`CircuitBreaker` (convenience factory / decorator)."""
    return CircuitBreaker(
        failure_threshold=failure_threshold,
        reset_timeout=reset_timeout,
        success_threshold=success_threshold,
        exceptions=exceptions,
        clock=clock,
    )


__all__: Iterable[str] = (
    "retry",
    "backoff_schedule",
    "CircuitBreaker",
    "BreakerState",
    "CircuitOpenError",
    "circuit_breaker",
)
