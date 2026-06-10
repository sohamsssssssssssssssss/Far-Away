"""Deployment-oriented load / throughput tests (PRD Step 10 — production CI).

This module drives a *large* number of independent incidents through the real,
fully-wired system (:func:`disastermind.orchestration.build.build_system` plus
the synthetic ``scenarios`` generators) and asserts **structural throughput
invariants** — never wall-clock latency. Two complementary surfaces are covered:

  1. **Local, deterministic, offline (always runs).** The in-memory bus fans out
     synchronously, so once we inject ``K`` incidents the whole reactive chain
     ``RAW_FEED -> PREDICTION -> ... -> DISPATCH`` has already fired. We assert:
       * every one of the ``K`` incidents reaches a real DISPATCH order;
       * message/dispatch volume scales (more in => at least as many out);
       * the bus history ring buffer stays bounded under sustained load;
       * per-cycle work stays under a generous budget measured with an
         **injected operation counter** (bus publishes) — NOT ``time.time``.
     Everything here is stdlib-only and offline (PRD HARD RULE 2): no network,
     no broker, no solver, no ML, no sleeping.

  2. **Optional live deployment probe (skipped by default).** Guarded by the
     ``DM_LOAD_URL`` environment variable — when *unset* (the CI/local default)
     the test is skipped and **no network call is ever made**. When an operator
     explicitly sets ``DM_LOAD_URL=https://...`` it hammers ``/healthz`` a few
     times and asserts the deployment answers. This is opt-in smoke load only.

Determinism: all budgets are injected (operation counter), all clocks are
injected (``clock=lambda: 0.0``), randomness is unused. Nothing is timing-based,
so nothing is flaky.
"""
from __future__ import annotations

import os

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import Message, Priority, Topic
from disastermind.orchestration.build import build_system
from disastermind.scenarios.base import (
    inject_raw_event,
    real_dispatches,
    seed_field_teams,
    topic_counts,
)


# --------------------------------------------------------------------------- helpers
def _flood_meta(i: int) -> dict:
    """A realistic, past-threshold cyclone/flood meta block for incident ``i``."""
    return {
        "system_name": f"Cyclone Deployed-{i}",
        "category": "Severe Cyclonic Storm",
        "max_wind_kmph": 135.0,
        "storm_surge_m": 3.2,
        "rainfall_mm": 190.0,
        "river_level_m": 6.6,
        "warning_colour": "red",
        "region": "Mahanadi delta, Odisha",
    }


def _inject_flood_incident(bus: InMemoryBus, i: int) -> Message:
    """Inject one independent, dispatchable cyclone/flood incident.

    Epicentres are spread by a tiny delta so every incident is distinct yet still
    sits inside the seeded team belt (so each can bind teams and genuinely
    dispatch). The incident id is namespaced ``deployed:`` to avoid colliding
    with the sibling ``test_load.py`` namespace.
    """
    from disastermind.core.contracts import Module

    return inject_raw_event(
        bus,
        kind="flood",
        module=Module.CYCLONE_FLOOD,
        incident_id=f"deployed:incident-{i}",
        lat=20.30 + 0.001 * i,
        lon=85.84 + 0.001 * i,
        severity=3.0,
        meta=_flood_meta(i),
        observations=[{"population": 1600}],
        priority=Priority.CRITICAL,
        reasoning=[f"synthetic deployed-load incident {i}"],
    )


def _drive_n_incidents(n: int) -> tuple[object, list[Message]]:
    """Build the full system, seed teams, inject ``n`` incidents; return loop+orders.

    Because the in-memory bus dispatches synchronously, every chain has already
    fired by the time this returns. No loop ticks are needed for the reactive
    pipeline. Returns the loop and the list of *real* DISPATCH orders.
    """
    bus = InMemoryBus()
    loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    # The system must boot fully clean — no degraded modules in the happy path.
    assert loop.degraded_modules == [], loop.degraded_modules
    seed_field_teams(bus)
    for i in range(n):
        _inject_flood_incident(bus, i)
    return loop, real_dispatches(loop)


class _PublishCounter:
    """A deterministic operation counter wrapping a bus's ``publish``.

    This is the injected "clock" for the step budget: instead of timing work with
    wall-clock, we count bus publishes (the unit of work a cycle performs). The
    count is fully deterministic, so the budget assertion is never flaky.
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


# ============================================================ local deterministic load
@pytest.mark.parametrize("n", [1, 8, 32])
def test_k_incidents_all_dispatch(n: int) -> None:
    """Every one of the ``K`` injected incidents must reach a real DISPATCH order."""
    _loop, orders = _drive_n_incidents(n)

    dispatched_ids = {m.incident_id for m in orders}
    expected_ids = {f"deployed:incident-{i}" for i in range(n)}

    assert expected_ids <= dispatched_ids, (
        f"missing dispatch for incidents {expected_ids - dispatched_ids}"
    )
    # At least one order per incident — never fewer orders than incidents.
    assert len(orders) >= n


def test_message_counts_scale_with_load() -> None:
    """More incidents in => at least as many dispatch orders AND more bus traffic.

    Throughput must be non-decreasing as load climbs (structural, not timed), and
    a heavier run must move strictly more total messages than a light one.
    """
    dispatch_counts: list[int] = []
    history_sizes: list[int] = []
    for n in (1, 2, 4, 8, 16):
        loop, orders = _drive_n_incidents(n)
        dispatch_counts.append(len(orders))
        history_sizes.append(len(loop.bus.history))

    # Non-decreasing dispatch throughput as load increases.
    assert dispatch_counts == sorted(dispatch_counts), dispatch_counts
    assert all(c >= 1 for c in dispatch_counts)

    # Total bus traffic strictly grows from the lightest to the heaviest run.
    assert history_sizes[-1] > history_sizes[0], history_sizes

    # The load-bearing topics all carry traffic at the heaviest level.
    loop_big, _ = _drive_n_incidents(16)
    counts = topic_counts(loop_big)
    for topic in (Topic.RAW_FEED, Topic.PREDICTION, Topic.DISPATCH):
        assert counts.get(topic, 0) > 0, (topic, counts)

    # Distinct incidents each landed at the heaviest level.
    _loop, orders = _drive_n_incidents(16)
    assert len({m.incident_id for m in orders}) == 16


def test_bus_history_bounded_under_sustained_load() -> None:
    """The in-memory bus ring buffer never grows past its configured bound.

    Pump far more incidents than the (small) cap and assert the invariant holds
    at *every* step, not just the end — while genuine throughput still happens.
    """
    cap = 64
    bus = InMemoryBus(history=cap)
    loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    assert loop.degraded_modules == [], loop.degraded_modules
    seed_field_teams(bus)

    produced = 0
    for i in range(50):  # 50 incidents >> cap worth of fan-out
        _inject_flood_incident(bus, i)
        produced += 1
        assert len(bus.history) <= cap, (i, len(bus.history))

    assert produced == 50
    # History is saturated (we produced far more than the cap) yet still bounded.
    assert len(bus.history) == cap


def test_per_cycle_work_under_injected_budget() -> None:
    """Per-cycle operation cost stays bounded across many cycles (injected counter).

    We measure *operations* (bus publishes), NOT wall-clock seconds, using a fully
    injected clock + no-op sleep. The average operations-per-cycle must stay under
    a generous fixed budget, proving work does not accumulate unboundedly.
    """
    bus = InMemoryBus()
    loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    seed_field_teams(bus)
    for i in range(4):
        _inject_flood_incident(bus, i)

    counter = _PublishCounter(bus)
    cycles = 12
    try:
        ran = loop.run(max_cycles=cycles, clock=lambda: 0.0, sleep=lambda _s: None)
    finally:
        counter.restore()

    assert ran == cycles
    per_cycle = counter.count / cycles
    # Generous, deterministic per-cycle ceiling — no timing dependency.
    assert per_cycle < 300, per_cycle


# ===================================================== optional live deployment probe
DM_LOAD_URL = os.environ.get("DM_LOAD_URL", "").strip()


@pytest.mark.skipif(
    not DM_LOAD_URL,
    reason="DM_LOAD_URL is not set — live deployment load probe is opt-in only "
    "(no network call is made in CI/local by default).",
)
def test_live_deployment_healthz_load() -> None:
    """OPT-IN: drive a handful of requests at a live deployment's ``/healthz``.

    Only runs when an operator explicitly exports ``DM_LOAD_URL``. Uses stdlib
    ``urllib`` (no third-party HTTP dep) and asserts the deployment answers 200
    on every probe. Skipped — and therefore network-free — by default.
    """
    import urllib.error
    import urllib.request

    base = DM_LOAD_URL.rstrip("/")
    url = f"{base}/healthz"
    probes = 5
    statuses: list[int] = []
    for _ in range(probes):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
                statuses.append(resp.status)
                resp.read(1024)  # drain a little to fully complete the request
        except urllib.error.URLError as exc:  # pragma: no cover - live-only path
            pytest.fail(f"live deployment probe failed for {url}: {exc}")

    assert len(statuses) == probes
    assert all(s == 200 for s in statuses), statuses
