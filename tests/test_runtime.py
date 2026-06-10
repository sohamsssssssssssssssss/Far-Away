"""Tests for the process runtime package (PRD Step 10).

Covers:
  * :class:`ProcessRunner` deterministic stepping — ``step``/``start`` advance
    ``CoordinationLoop.run_once`` with an injected clock and a no-op sleep, never
    really sleeping, and honour ``max_cycles``.
  * :class:`KafkaConsumerRuntime` degrades cleanly with no broker (in-memory bus
    and degraded ``KafkaBus``): no real consumer thread, ``degraded`` set, and
    subscriptions wired onto the in-memory fallback.

Stdlib-only and network-free; the real confluent_kafka path is import-skipped.
"""
from __future__ import annotations

import threading

import pytest

from disastermind.core.bus import InMemoryBus, KafkaBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.runtime import KafkaConsumerRuntime, ProcessRunner


# --------------------------------------------------------------------------- #
# ProcessRunner — deterministic stepping
# --------------------------------------------------------------------------- #
def _runner() -> ProcessRunner:
    return ProcessRunner(
        bus=InMemoryBus(), settings=Settings(), install_signals=False
    )


def test_step_advances_one_cycle_without_sleeping():
    runner = _runner()
    assert runner.cycle == 0
    assert runner.step(now_epoch=1000.0) == 1
    assert runner.step(now_epoch=1030.0) == 2
    assert runner.cycle == 2


def test_start_honours_max_cycles_and_never_really_sleeps():
    runner = _runner()

    slept: list[float] = []
    clock_value = {"t": 0.0}

    def fake_clock() -> float:
        clock_value["t"] += 30.0
        return clock_value["t"]

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    executed = runner.start(max_cycles=4, clock=fake_clock, sleep=fake_sleep)

    assert executed == 4, "max_cycles not honoured"
    assert runner.cycle == 4
    # run() breaks *before* sleeping on the final cycle, so at most max_cycles-1.
    assert len(slept) <= 3
    # Whatever sleeping happened was the injected no-op at the loop interval.
    assert all(s == runner.settings.loop_interval_seconds for s in slept)
    assert runner.running is False


def test_start_uses_injected_clock_for_now_epoch(monkeypatch):
    """The injected clock must feed run_once's now_epoch (no time.time())."""
    runner = _runner()
    seen: list[float | None] = []

    original = runner.loop.run_once

    def spy(now_epoch=None):
        seen.append(now_epoch)
        return original(now_epoch)

    monkeypatch.setattr(runner.loop, "run_once", spy)

    runner.start(max_cycles=3, clock=lambda: 4242.0, sleep=lambda _s: None)
    assert seen == [4242.0, 4242.0, 4242.0]


def test_stop_halts_the_loop():
    runner = _runner()
    runner.loop.disaster_active = True
    runner.stop()
    assert runner.loop.disaster_active is False
    assert runner.running is False


def test_runner_builds_full_dag_and_reaches_dispatch():
    """A stepped runner over the in-memory bus still drives the full pipeline."""
    bus = InMemoryBus()
    runner = ProcessRunner(bus=bus, settings=Settings(), install_signals=False)

    # Seed field teams then drive ingestion the way the e2e harness does.
    readings = [
        {
            "team_id": "NDRF-01",
            "asset_type": "ndrf_team",
            "location": {"lat": 20.30, "lon": 85.82},
            "status": "idle",
        }
    ]
    bus.publish(
        Message(
            sender="iot.gps_beacon",
            recipient="broadcast",
            type=MessageType.QUERY,
            priority=Priority.INFO,
            topic=Topic.IOT_TELEMETRY,
            module=Module.ALL,
            payload={"kind": "gps_beacon", "readings": readings},
        )
    )
    runner.step(now_epoch=0.0)

    counts: dict[str, int] = {}
    for m in bus.history:
        counts[m.topic] = counts.get(m.topic, 0) + 1
    assert counts.get(Topic.RAW_FEED, 0) > 0
    assert counts.get(Topic.PREDICTION, 0) > 0


# --------------------------------------------------------------------------- #
# KafkaConsumerRuntime — clean degradation with no broker
# --------------------------------------------------------------------------- #
def test_consumer_degrades_on_in_memory_bus():
    rt = KafkaConsumerRuntime(InMemoryBus())
    assert rt.degraded is True
    # start() is a clean no-op returning False; no real thread spun up.
    assert rt.start() is False
    assert rt.running is False
    rt.stop()  # idempotent / safe


def test_consumer_degrades_on_unreachable_kafka_bus():
    # No reachable broker -> KafkaBus.degraded True (or no producer) -> no-op.
    bus = KafkaBus(brokers="broker-does-not-exist:9092")
    rt = KafkaConsumerRuntime(bus)
    assert rt.degraded is True
    assert rt.start() is False
    assert rt.running is False


def test_degraded_subscribe_wires_inmemory_fallback_and_delivers():
    bus = InMemoryBus()
    rt = KafkaConsumerRuntime(bus)
    assert rt.degraded is True

    received: list[Message] = []
    rt.subscribe(Topic.DISPATCH, lambda m: received.append(m))

    msg = Message(
        sender="commander",
        recipient="dispatch",
        type=MessageType.INSTRUCTION,
        priority=Priority.CRITICAL,
        topic=Topic.DISPATCH,
        module=Module.EARTHQUAKE,
        payload={"kind": "dispatch"},
    )
    bus.publish(msg)
    assert received and received[0] is msg


def test_consumer_constructor_subscriptions_recorded():
    cb_calls: list[Message] = []
    rt = KafkaConsumerRuntime(
        InMemoryBus(), subscriptions={Topic.DISPATCH: lambda m: cb_calls.append(m)}
    )
    assert Topic.DISPATCH in rt.subscriptions
    assert rt.degraded is True


def test_consumer_no_thread_leak_in_degraded_mode():
    before = threading.active_count()
    rt = KafkaConsumerRuntime(InMemoryBus())
    rt.start()
    rt.stop()
    assert threading.active_count() == before


def test_runner_consumer_is_degraded_with_default_bus():
    runner = _runner()
    assert runner.consumer.degraded is True
    # Starting/stopping the whole runner with a degraded consumer is safe.
    runner.start(max_cycles=1, clock=lambda: 1.0, sleep=lambda _s: None)
    assert runner.consumer.running is False


# --------------------------------------------------------------------------- #
# Optional: live confluent_kafka path is skipped when the lib is absent
# --------------------------------------------------------------------------- #
def test_live_path_requires_confluent_kafka():
    pytest.importorskip("confluent_kafka")
    # If the lib *is* present but no broker is reachable, the runtime must still
    # degrade rather than raise on a real bus with no producer.
    bus = KafkaBus(brokers="broker-does-not-exist:9092")
    rt = KafkaConsumerRuntime(bus)
    assert rt.degraded is True
