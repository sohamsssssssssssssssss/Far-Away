"""Tests for the operational-hardening package ``disastermind.ops``.

Offline, stdlib-only, fully deterministic (PRD HARD RULE 2):

  * the circuit breaker opens after N consecutive failures, fast-fails while
    open, then half-opens after the cooldown (driven by an *injected* clock —
    never real time);
  * the retry decorator retries the right number of times with a deterministic
    exponential backoff (driven by an *injected* sleep — never real sleeping);
  * :func:`validate_settings` flags a bad setting and passes a good one;
  * :func:`readiness` reflects a freshly built coordination loop, and
    :func:`liveness` is always alive;
  * the :class:`GracefulShutdown` handler runs its drain callbacks once, in
    order, tolerating a raising callback — exercised via ``trigger`` directly
    (we never raise a real OS signal in a test).
"""
from __future__ import annotations

import pytest

from disastermind.core.config import Settings
from disastermind.ops import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
    GracefulShutdown,
    Severity,
    backoff_schedule,
    liveness,
    readiness,
    retry,
    validate_settings,
)


# --------------------------------------------------------------- circuit breaker
class _FakeClock:
    """A manually-advanced monotonic clock for deterministic cooldown tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def _boom() -> None:
    raise ValueError("boom")


def test_breaker_opens_after_n_failures() -> None:
    clock = _FakeClock()
    br = CircuitBreaker(failure_threshold=3, reset_timeout=10.0, clock=clock)

    # First two failures: still closed (consecutive failures < threshold).
    for _ in range(2):
        with pytest.raises(ValueError):
            br.call(_boom)
        assert br.state is BreakerState.CLOSED

    # Third consecutive failure trips it open.
    with pytest.raises(ValueError):
        br.call(_boom)
    assert br.state is BreakerState.OPEN
    assert br.is_open is True


def test_breaker_fast_fails_while_open() -> None:
    clock = _FakeClock()
    br = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, clock=clock)

    with pytest.raises(ValueError):
        br.call(_boom)
    assert br.state is BreakerState.OPEN

    # While open and inside the cooldown, calls are rejected fast WITHOUT
    # invoking the wrapped function.
    calls = {"n": 0}

    def _spy() -> str:
        calls["n"] += 1
        return "ok"

    with pytest.raises(CircuitOpenError):
        br.call(_spy)
    assert calls["n"] == 0  # the breaker never called through


def test_breaker_half_opens_after_cooldown_then_closes_on_success() -> None:
    clock = _FakeClock()
    br = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, clock=clock)

    with pytest.raises(ValueError):
        br.call(_boom)
    assert br.state is BreakerState.OPEN

    # Cooldown not yet elapsed -> still rejected.
    clock.advance(9.0)
    with pytest.raises(CircuitOpenError):
        br.call(lambda: "x")
    assert br.state is BreakerState.OPEN

    # Cooldown elapsed -> the next call probes (half-open) and, on success,
    # the breaker closes.
    clock.advance(2.0)  # total 11s >= 10s reset_timeout
    assert br.allows_request() is True
    assert br.state is BreakerState.HALF_OPEN
    assert br.call(lambda: "recovered") == "recovered"
    assert br.state is BreakerState.CLOSED
    assert br.failure_count == 0


def test_breaker_reopens_on_failure_during_half_open() -> None:
    clock = _FakeClock()
    br = CircuitBreaker(failure_threshold=1, reset_timeout=5.0, clock=clock)

    with pytest.raises(ValueError):
        br.call(_boom)
    assert br.state is BreakerState.OPEN

    clock.advance(5.0)  # cooldown elapsed -> half-open on next attempt
    with pytest.raises(ValueError):
        br.call(_boom)  # probe fails
    assert br.state is BreakerState.OPEN
    # And the cooldown clock restarts from the new opened_at.
    assert br.opened_at == clock.now


def test_breaker_success_resets_failure_count() -> None:
    clock = _FakeClock()
    br = CircuitBreaker(failure_threshold=3, reset_timeout=5.0, clock=clock)
    with pytest.raises(ValueError):
        br.call(_boom)
    with pytest.raises(ValueError):
        br.call(_boom)
    assert br.failure_count == 2
    br.call(lambda: "ok")  # a success in CLOSED state clears the streak
    assert br.failure_count == 0
    assert br.state is BreakerState.CLOSED


# ----------------------------------------------------------------------- retry
def test_retry_retries_expected_number_of_times_then_raises() -> None:
    slept: list[float] = []
    calls = {"n": 0}

    @retry(attempts=3, base_delay=0.5, factor=2.0, sleep=slept.append)
    def always_fails() -> None:
        calls["n"] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError):
        always_fails()

    assert calls["n"] == 3  # the full attempt budget was used
    # Two waits between three attempts, with deterministic exponential backoff.
    assert slept == [0.5, 1.0]


def test_retry_succeeds_after_transient_failures() -> None:
    slept: list[float] = []
    calls = {"n": 0}

    @retry(attempts=5, base_delay=1.0, factor=2.0, sleep=slept.append)
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3
    # Slept only before the two retries; no sleep after the successful call.
    assert slept == [1.0, 2.0]


def test_retry_does_not_retry_unlisted_exceptions() -> None:
    slept: list[float] = []
    calls = {"n": 0}

    @retry(attempts=3, exceptions=ConnectionError, sleep=slept.append)
    def raises_type_error() -> None:
        calls["n"] += 1
        raise TypeError("programming error, do not retry")

    with pytest.raises(TypeError):
        raises_type_error()
    assert calls["n"] == 1  # no retry for an unlisted exception type
    assert slept == []


def test_retry_respects_max_delay_clamp() -> None:
    assert backoff_schedule(5, base_delay=1.0, factor=3.0) == [1.0, 3.0, 9.0, 27.0]
    assert backoff_schedule(5, base_delay=1.0, factor=3.0, max_delay=5.0) == [
        1.0,
        3.0,
        5.0,
        5.0,
    ]
    assert backoff_schedule(1) == []  # one attempt -> no waits


def test_retry_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError):
        retry(attempts=0)


# ------------------------------------------------------------------ config check
def test_config_validation_flags_bad_setting() -> None:
    bad = Settings()
    bad.loop_interval_seconds = 0          # ERROR: must be > 0
    bad.postgres_dsn = "not-a-dsn"         # ERROR: malformed DSN scheme

    issues = validate_settings(bad)
    by_field = {i.field: i for i in issues}

    assert "loop_interval_seconds" in by_field
    assert by_field["loop_interval_seconds"].severity is Severity.ERROR
    assert "postgres_dsn" in by_field
    assert by_field["postgres_dsn"].severity is Severity.ERROR


def test_config_validation_passes_good_setting() -> None:
    good = Settings()  # the defaults are well-formed (loop interval, valid DSNs)
    issues = validate_settings(good)
    errors = [i for i in issues if i.severity is Severity.ERROR]
    assert errors == [], [str(i) for i in errors]


def test_config_validation_flags_negative_timeout_and_empty_dsn() -> None:
    bad = Settings()
    bad.escalation_timeout_seconds = -5
    bad.timescale_dsn = ""
    issues = validate_settings(bad)
    fields = {i.field for i in issues if i.severity is Severity.ERROR}
    assert "escalation_timeout_seconds" in fields
    assert "timescale_dsn" in fields


def test_config_validation_flags_kafka_without_brokers() -> None:
    bad = Settings()
    bad.use_kafka = True
    bad.kafka_brokers = ""
    issues = validate_settings(bad)
    assert any(
        i.field == "kafka_brokers" and i.severity is Severity.ERROR for i in issues
    )


# ----------------------------------------------------------------------- health
def test_readiness_reflects_a_built_loop() -> None:
    from disastermind.orchestration.build import build_system

    loop = build_system()
    rep = readiness(loop)

    assert rep["status"] == "ready"
    assert rep["ready"] is True
    assert rep["checks"]["agents"] == "ok"
    assert rep["checks"]["modules"] == "ok"
    assert rep["checks"]["bus"] == "ok"
    assert rep["detail"]["agent_count"] > 0
    assert rep["detail"]["degraded_modules"] == []


def test_readiness_not_ready_for_empty_loop() -> None:
    # A duck-typed loop with no agents and a degraded module is not ready.
    class _Loop:
        agents: list = []
        degraded_modules = ["disastermind.tier3.social.build"]
        bus = None
        disaster_active = False
        cycle = 0

    rep = readiness(_Loop())
    assert rep["status"] == "not_ready"
    assert rep["ready"] is False
    assert rep["checks"]["agents"] == "fail"
    assert rep["checks"]["modules"] == "fail"
    assert rep["checks"]["bus"] == "fail"


def test_readiness_never_raises_on_none() -> None:
    rep = readiness(None)
    assert rep["ready"] is False
    assert rep["status"] == "not_ready"


def test_liveness_is_always_alive() -> None:
    rep = liveness()
    assert rep["status"] == "alive"
    assert rep["live"] is True
    assert rep["checks"]["process"] == "ok"


# --------------------------------------------------------------------- shutdown
def test_graceful_shutdown_runs_drains_in_order_once() -> None:
    order: list[str] = []
    gs = GracefulShutdown()
    gs.register(lambda: order.append("persist-state"), name="persist-state")
    gs.register(lambda: order.append("flush-audit"), name="flush-audit")
    gs.register(lambda: order.append("stop-loop"), name="stop-loop")

    assert gs.triggered is False
    did = gs.trigger("SIGTERM")
    assert did is True
    assert gs.triggered is True
    assert gs.reason == "SIGTERM"
    assert order == ["persist-state", "flush-audit", "stop-loop"]

    # Idempotent: a second trigger does nothing (drains run once).
    order.clear()
    again = gs.trigger("SIGINT")
    assert again is False
    assert order == []


def test_graceful_shutdown_continues_past_a_raising_callback() -> None:
    order: list[str] = []
    gs = GracefulShutdown()
    gs.register(lambda: order.append("a"), name="a")

    def _bad() -> None:
        raise RuntimeError("drain failed")

    gs.register(_bad, name="bad")
    gs.register(lambda: order.append("c"), name="c")

    gs.trigger("SIGTERM")
    # The raising callback did not abort the remaining drains.
    assert order == ["a", "c"]
    assert len(gs.errors) == 1
    assert gs.errors[0][0] == "bad"
    assert isinstance(gs.errors[0][1], RuntimeError)


def test_graceful_shutdown_reverse_order() -> None:
    order: list[str] = []
    gs = GracefulShutdown(reverse=True)
    gs.register(lambda: order.append("first"), name="first")
    gs.register(lambda: order.append("second"), name="second")
    gs.trigger("manual")
    assert order == ["second", "first"]


def test_graceful_shutdown_handler_triggers_without_real_signal() -> None:
    # Drive the OS-handler entry point directly with a synthetic signum/frame so
    # we exercise the handler logic WITHOUT installing or raising a real signal.
    import signal as _signal

    fired: list[str] = []
    gs = GracefulShutdown()
    gs.register(lambda: fired.append("drained"), name="drain")
    gs._handler(_signal.SIGTERM, None)
    assert fired == ["drained"]
    assert gs.triggered is True
    assert gs.reason == "SIGTERM"
