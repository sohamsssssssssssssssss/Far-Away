"""Throughput / structural load tests (PRD Step 10 — production reliability).

These tests drive *many* incidents through the real, fully-wired system
(:func:`disastermind.orchestration.build.build_system` plus the synthetic
``scenarios`` generators) and assert **throughput and structural invariants**
deterministically. They are emphatically NOT timing benchmarks:

  * every assertion is on a *count* (incidents dispatched, messages produced,
    history length) — never on wall-clock latency, so nothing is flaky;
  * the only "budget" we assert is a *step budget* measured with an **injected
    operation counter** (bus publishes), not :func:`time.time`. A single
    ``run_once`` must stay under a generous, deterministic operation budget.

Offline and stdlib-only by construction (PRD HARD RULE 2): the in-memory bus
fans out synchronously, so after we inject N incidents the whole chain
``RAW_FEED -> PREDICTION -> ... -> DISPATCH`` has already fired. No network, no
broker, no solver, no ML, no sleeping.
"""
from __future__ import annotations

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import Message, MessageType, Module, Priority, Topic
from disastermind.orchestration.build import build_system
from disastermind.ops import LatencyBudget, Timer
from disastermind.scenarios.base import (
    inject_raw_event,
    real_dispatches,
    seed_field_teams,
    topic_counts,
)
from disastermind.scenarios import (
    simulate_cyclone_flood,
    simulate_earthquake,
    simulate_urban_fire,
)


# --------------------------------------------------------------------------- helpers
def _flood_meta(i: int) -> dict:
    """A realistic, past-threshold cyclone/flood meta block for incident ``i``."""
    return {
        "system_name": f"Cyclone Load-{i}",
        "category": "Severe Cyclonic Storm",
        "max_wind_kmph": 130.0,
        "storm_surge_m": 3.0,
        "rainfall_mm": 180.0,
        "river_level_m": 6.5,
        "warning_colour": "red",
        "region": "Mahanadi delta, Odisha",
    }


def _inject_flood_incident(bus: InMemoryBus, i: int) -> Message:
    """Inject one independent, dispatchable cyclone/flood incident."""
    return inject_raw_event(
        bus,
        kind="flood",
        module=Module.CYCLONE_FLOOD,
        incident_id=f"load:incident-{i}",
        # Spread epicentres slightly so each incident is distinct but all sit
        # inside the seeded team belt (so each can bind teams and dispatch).
        lat=20.30 + 0.001 * i,
        lon=85.84 + 0.001 * i,
        severity=3.0,
        meta=_flood_meta(i),
        observations=[{"population": 1500}],
        priority=Priority.CRITICAL,
        reasoning=[f"synthetic load incident {i}"],
    )


def _drive_n_incidents(n: int) -> tuple[object, list[Message]]:
    """Build the full system, seed teams, inject ``n`` incidents; return loop+orders.

    Because the in-memory bus dispatches synchronously, every chain has already
    fired by the time this returns — no loop ticks are needed for the reactive
    pipeline. Returns the loop and the list of real DISPATCH orders.
    """
    bus = InMemoryBus()
    loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    # The system must boot fully clean — no degraded modules in the happy path.
    assert loop.degraded_modules == [], loop.degraded_modules
    seed_field_teams(bus)
    for i in range(n):
        _inject_flood_incident(bus, i)
    return loop, real_dispatches(loop)


# ----------------------------------------------------------------- K reach dispatch
@pytest.mark.parametrize("n", [1, 5, 20])
def test_every_injected_incident_reaches_dispatch(n: int) -> None:
    """All K injected incidents must reach a real DISPATCH order (throughput)."""
    loop, orders = _drive_n_incidents(n)

    dispatched_ids = {m.incident_id for m in orders}
    expected_ids = {f"load:incident-{i}" for i in range(n)}

    # Every incident produced at least one genuine dispatch order.
    assert expected_ids <= dispatched_ids, (
        f"missing dispatch for incidents {expected_ids - dispatched_ids}"
    )
    # And there are at least as many orders as incidents (one+ per incident).
    assert len(orders) >= n


def test_dispatch_count_grows_monotonically_with_incident_count() -> None:
    """More incidents in => at least as many dispatch orders out (structural)."""
    counts: list[int] = []
    for n in (1, 2, 4, 8):
        _loop, orders = _drive_n_incidents(n)
        counts.append(len(orders))

    # Non-decreasing throughput as load increases, and every level dispatched.
    assert counts == sorted(counts), counts
    assert all(c >= 1 for c in counts)
    # Distinct incidents each landed: the largest run dispatched >= 8 incidents.
    loop, orders = _drive_n_incidents(8)
    assert len({m.incident_id for m in orders}) == 8


def test_message_volume_grows_with_load() -> None:
    """Total bus traffic strictly grows from 1 incident to many (structural)."""
    loop_small, _ = _drive_n_incidents(1)
    loop_big, _ = _drive_n_incidents(10)

    small = len(loop_small.bus.history)
    big = len(loop_big.bus.history)
    assert big > small, (small, big)

    # The load-bearing topics all carry traffic under load.
    counts = topic_counts(loop_big)
    for topic in (Topic.RAW_FEED, Topic.PREDICTION, Topic.DISPATCH):
        assert counts.get(topic, 0) > 0, (topic, counts)


# ------------------------------------------------------------- bounded bus history
def test_bus_history_stays_bounded_under_sustained_load() -> None:
    """The in-memory bus ring buffer never grows past its configured bound.

    Under sustained load the history must not leak unboundedly — it is a ring
    buffer. We cap it small and pump far more incidents than the cap, then assert
    the invariant holds at the end AND that genuine throughput still happened.
    """
    cap = 64
    bus = InMemoryBus(history=cap)
    loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    seed_field_teams(bus)

    produced = 0
    for i in range(40):  # 40 incidents >> cap worth of fan-out
        _inject_flood_incident(bus, i)
        produced += 1
        # Invariant holds at every step, not just the end.
        assert len(bus.history) <= cap, (i, len(bus.history))

    assert produced == 40
    # The history is full (we produced far more than the cap) but still bounded.
    assert len(bus.history) == cap


# -------------------------------------------------- step budget (injected counter)
class _OpCounter:
    """A deterministic operation counter that wraps a bus's ``publish``.

    This is the injected "clock" for the step budget: instead of timing the step
    with wall-clock, we count the number of bus publishes (the unit of work a
    cycle performs). The count is fully deterministic, so the budget assertion is
    not flaky.
    """

    def __init__(self, bus: InMemoryBus) -> None:
        self._bus = bus
        self.count = 0
        self._orig_publish = bus.publish
        bus.publish = self._counting_publish  # type: ignore[method-assign]

    def _counting_publish(self, message: Message) -> None:
        self.count += 1
        self._orig_publish(message)

    def restore(self) -> None:
        self._bus.publish = self._orig_publish  # type: ignore[method-assign]


def test_single_run_once_stays_under_a_generous_step_budget() -> None:
    """One ``run_once`` performs a bounded number of operations (injected counter).

    We measure *operations* (bus publishes), NOT wall-clock seconds. After
    injecting a handful of incidents we drive exactly one coordination cycle and
    assert it issued fewer than a generous fixed operation budget. This proves the
    per-step cost is bounded without any timing dependency.
    """
    bus = InMemoryBus()
    loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    seed_field_teams(bus)
    for i in range(3):
        _inject_flood_incident(bus, i)

    # Start counting only the work the single cycle does.
    counter = _OpCounter(bus)
    try:
        loop.run_once(now_epoch=0.0)
    finally:
        counter.restore()

    # Generous, deterministic budget: a single periodic sweep over the wired
    # agents must not explode. (Observed publishes per idle-ish tick are tiny;
    # 500 is comfortably generous yet still bounded.)
    STEP_OP_BUDGET = 500
    assert counter.count < STEP_OP_BUDGET, counter.count
    # And the cycle actually advanced.
    assert loop.cycle == 1


def test_run_loop_step_budget_holds_across_many_cycles() -> None:
    """Driving many cycles keeps per-cycle operation cost bounded (no growth leak).

    Using injected ``clock``/``sleep`` (no real time), run several cycles and
    assert the *average* operations-per-cycle stays under a generous budget — so
    work does not accumulate unboundedly across cycles.
    """
    bus = InMemoryBus()
    loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    seed_field_teams(bus)
    for i in range(2):
        _inject_flood_incident(bus, i)

    counter = _OpCounter(bus)
    cycles = 10
    try:
        # Fully injected clock + no-op sleep: deterministic, no wall-clock.
        ran = loop.run(max_cycles=cycles, clock=lambda: 0.0, sleep=lambda _s: None)
    finally:
        counter.restore()

    assert ran == cycles
    per_cycle = counter.count / cycles
    assert per_cycle < 200, per_cycle  # generous bound on steady-state per-cycle work


# ----------------------------------------------------- ops budget primitives (unit)
def test_latency_budget_reports_within_and_overrun_deterministically() -> None:
    """``LatencyBudget`` uses an injected clock — within/overrun are exact."""

    class _Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

        def advance(self, dt: float) -> None:
            self.t += dt

    clock = _Clock()
    with LatencyBudget(0.250, name="step", clock=clock) as b:
        clock.advance(0.100)  # 100 ms — well within a 250 ms budget
    assert b.within_budget is True
    assert b.overrun == 0.0
    assert b.elapsed == pytest.approx(0.100)

    clock2 = _Clock()
    with LatencyBudget(0.050, name="step", clock=clock2) as over:
        clock2.advance(0.200)  # 200 ms — over a 50 ms budget
    assert over.within_budget is False
    assert over.overrun == pytest.approx(0.150)
    # An overrun is reported, never raised, by the context manager itself.


def test_timer_measures_with_injected_clock() -> None:
    """``Timer`` elapsed is driven purely by the injected clock (no real time)."""

    ticks = iter([0.0, 0.5])  # __enter__ reads 0.0, __exit__ reads 0.5

    def clock() -> float:
        return next(ticks)

    with Timer(name="work", clock=clock) as t:
        pass
    assert t.elapsed == pytest.approx(0.5)
    assert t.running is False


# --------------------------------------------------- all three modules under load
def test_all_three_scenario_modules_dispatch() -> None:
    """Each module's scenario generator independently reaches DISPATCH (coverage)."""
    for gen in (simulate_cyclone_flood, simulate_earthquake, simulate_urban_fire):
        loop = gen()
        orders = real_dispatches(loop)
        assert orders, f"{gen.__name__} produced no dispatch orders"
        assert loop.degraded_modules == []
