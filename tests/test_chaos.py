"""Chaos / graceful-degradation tests (PRD Step 10 — production reliability).

Where ``test_load.py`` proves the system scales, this proves it *survives*. Each
test injects a deliberate fault and asserts the load-bearing chain still reaches
``Topic.DISPATCH`` (or degrades cleanly):

  (a) an agent that raises in ``tick()`` / ``handle()`` does NOT stop the loop or
      its sibling agents — the pipeline still dispatches;
  (b) ``build_system`` with a deliberately-broken module path records it in
      ``loop.degraded_modules`` yet the rest of the DAG still dispatches;
  (c) a ``KafkaBus`` with an unreachable broker reports ``degraded=True`` and
      transparently falls back to an in-memory bus (lazy-import / connection
      fallback ONLY — never a real broker);
  (d) :class:`~disastermind.ops.GracefulShutdown` runs its drain callbacks (in
      order, once, tolerating a raising callback);
  (e) the new :class:`~disastermind.ops.ReadinessAggregator` correctly reports
      *not ready* when a wired-in chaos signal is degraded, and never crashes on a
      signal that raises.

This file reuses the already-tested, stdlib-only helpers from
:mod:`disastermind.resilience` so it never touches a broker, solver or ML stack,
and adds the ops-layer drain / readiness-aggregator proofs that ``ops`` now owns.
Fully offline and deterministic (PRD HARD RULE 2 / Step 10).
"""
from __future__ import annotations

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus, KafkaBus
from disastermind.core.config import Settings
from disastermind.core.contracts import Message, MessageType, Module, Priority, Topic
from disastermind.orchestration.build import build_system
from disastermind.ops import GracefulShutdown, ReadinessAggregator, readiness
from disastermind.resilience import (
    BROKEN_MODULE_PATH,
    FailingAgent,
    build_with_broken_module,
    degraded_kafka_bus,
    drive_to_dispatch,
    inject_failing_agent,
    last_known_orders,
    seed_field_teams,
)


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
def system():
    """A freshly-wired full DAG on an in-memory bus (null audit logger)."""
    bus = InMemoryBus()
    return build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())


# --------------------------------------------------------------------------- (a)
class TestAgentFailureIsolation:
    """A raising agent must not stop the loop or its siblings (PRD Step 10)."""

    def test_failing_tick_does_not_stop_run_once(self, system) -> None:
        """A chaos agent raising every ``tick()`` does not abort ``run_once``."""
        bad = inject_failing_agent(system.bus, subscriptions=[])
        system.agents.append(bad)

        # The loop sweeps every agent; the failing tick is swallowed, the cycle
        # completes, and the counter advances.
        cycle = system.run_once(now_epoch=0.0)
        assert cycle == 1
        assert bad.raised >= 1  # the failure path was actually exercised

    def test_failing_handle_does_not_break_dispatch_pipeline(self, system) -> None:
        """A chaos agent raising in ``handle()`` still lets the chain dispatch.

        The failing agent subscribes to the GPS-beacon telemetry topic that the
        harness seeds, so its ``handle()`` raises during the very first fan-out —
        yet every sibling still runs and the pipeline reaches DISPATCH.
        """
        bad = inject_failing_agent(system.bus, subscriptions=[Topic.IOT_TELEMETRY])
        orders = drive_to_dispatch(system)

        assert bad.raised >= 1, "the failing handle() path was never exercised"
        assert orders, "a sibling-agent failure stopped the dispatch pipeline"

    def test_bus_publish_isolates_a_raising_subscriber(self) -> None:
        """``InMemoryBus.publish`` must not let one bad subscriber break delivery."""
        bus = InMemoryBus()
        delivered: list[str] = []

        def good(_m: Message) -> None:
            delivered.append("good")

        def bad(_m: Message) -> None:
            raise RuntimeError("subscriber boom")

        bus.subscribe("t", "bad", bad)
        bus.subscribe("t", "good", good)
        bus.publish(
            Message(
                sender="s",
                recipient="r",
                type=MessageType.ALERT,
                priority=Priority.INFO,
                topic="t",
            )
        )
        # The healthy subscriber still received the message despite the bad one.
        assert delivered == ["good"]
        # And the message is still in history (publish completed).
        assert len(bus.history) == 1

    def test_failing_agent_is_zero_authority_tier3(self, system) -> None:
        """The chaos probe is a real zero-authority Tier-3 agent (sanity)."""
        bad = FailingAgent(system.bus)
        assert bad.decision_authority is False


# --------------------------------------------------------------------------- (b)
class TestBrokenModuleDegradation:
    """A broken module is isolated; the rest of the DAG still dispatches."""

    def test_broken_module_lands_in_degraded_modules(self) -> None:
        loop = build_with_broken_module()
        assert BROKEN_MODULE_PATH in loop.degraded_modules

    def test_broken_module_still_reaches_dispatch(self) -> None:
        loop = build_with_broken_module()
        orders = drive_to_dispatch(loop)
        assert orders, "a broken module stopped the dispatch pipeline"

    def test_healthy_modules_all_wire_alongside_the_broken_one(self) -> None:
        """Only the injected bad path is degraded; every real module loads."""
        loop = build_with_broken_module()
        assert loop.degraded_modules == [BROKEN_MODULE_PATH]
        assert len(loop.agents) > 0

    def test_build_with_broken_module_restores_global_paths(self) -> None:
        """The helper must not leave ``MODULE_BUILD_PATHS`` permanently mutated."""
        from disastermind.orchestration import loop as loop_mod

        before = list(loop_mod.MODULE_BUILD_PATHS)
        build_with_broken_module()
        assert loop_mod.MODULE_BUILD_PATHS == before
        # A subsequent clean build has no degraded modules.
        clean = build_system(bus=InMemoryBus())
        assert clean.degraded_modules == []


# --------------------------------------------------------------------------- (c)
class TestKafkaBusFailover:
    """KafkaBus degrades to in-memory when the broker is unreachable (no network)."""

    def test_unreachable_broker_reports_degraded(self) -> None:
        bus = degraded_kafka_bus()
        assert isinstance(bus, KafkaBus)
        assert bus.degraded is True
        assert bus._producer is None  # no real client in stdlib-only env

    def test_degraded_kafka_delivers_via_inmemory_fallback(self) -> None:
        """Publish on a degraded KafkaBus still fans out to subscribers."""
        bus = degraded_kafka_bus()
        received: list[Message] = []
        bus.subscribe("t", "sub", received.append)
        msg = Message(
            sender="s",
            recipient="r",
            type=MessageType.ALERT,
            priority=Priority.INFO,
            topic="t",
        )
        bus.publish(msg)
        assert received == [msg]

    def test_full_system_dispatches_on_degraded_kafka_bus(self) -> None:
        """The whole DAG reaches DISPATCH even wired on a degraded KafkaBus."""
        bus = degraded_kafka_bus()
        loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
        orders = drive_to_dispatch(loop)
        assert bus.degraded is True
        assert orders, "degraded KafkaBus failover did not reach DISPATCH"

    def test_failover_uses_unreachable_invalid_tld_never_a_real_broker(self) -> None:
        """The brokers use the reserved ``.invalid`` TLD — never a real broker."""
        bus = degraded_kafka_bus()
        assert ".invalid" in bus.brokers
        assert bus.backup_brokers is not None and ".invalid" in bus.backup_brokers


# --------------------------------------------------------------------------- (d)
class TestGracefulShutdownDrain:
    """GracefulShutdown runs its drain callbacks (ops-owned, PRD Step 10)."""

    def test_drains_run_in_order_exactly_once(self) -> None:
        order: list[str] = []
        gs = GracefulShutdown()
        gs.register(lambda: order.append("persist"), name="persist")
        gs.register(lambda: order.append("flush-audit"), name="flush-audit")
        gs.register(lambda: order.append("stop-loop"), name="stop-loop")

        assert gs.trigger("SIGTERM") is True
        assert order == ["persist", "flush-audit", "stop-loop"]
        # Idempotent — a second trigger does nothing.
        order.clear()
        assert gs.trigger("SIGINT") is False
        assert order == []

    def test_drain_continues_past_a_raising_callback(self) -> None:
        order: list[str] = []
        gs = GracefulShutdown()
        gs.register(lambda: order.append("a"), name="a")

        def bad() -> None:
            raise RuntimeError("drain failed")

        gs.register(bad, name="bad")
        gs.register(lambda: order.append("c"), name="c")

        gs.trigger("SIGTERM")
        # The raising callback did not abort the remaining drains.
        assert order == ["a", "c"]
        assert len(gs.errors) == 1 and gs.errors[0][0] == "bad"

    def test_shutdown_drains_a_live_loop_without_real_signal(self) -> None:
        """Wire a real loop's ``stop`` as a drain; trigger via the handler entry."""
        import signal as _signal

        loop = build_system(bus=InMemoryBus())
        loop.disaster_active = True
        gs = GracefulShutdown()
        gs.register(loop.stop, name="stop-loop")

        # Drive the OS-handler entry point directly with a synthetic signum —
        # never install or raise a real signal.
        gs._handler(_signal.SIGTERM, None)
        assert gs.triggered is True
        assert loop.disaster_active is False  # the drain actually stopped the loop


# --------------------------------------------------------------------------- (e)
class TestReadinessAggregatorUnderChaos:
    """The new ops readiness aggregator reports degradation; never crashes."""

    def test_aggregator_is_ready_for_a_healthy_loop(self) -> None:
        loop = build_system(bus=InMemoryBus())
        agg = ReadinessAggregator()
        agg.register("loop", lambda: readiness(loop)["ready"])
        agg.register("bus-live", lambda: loop.bus is not None)
        assert agg.is_ready() is True
        assert agg.evaluate()["status"] == "ready"

    def test_aggregator_not_ready_when_a_module_is_degraded(self) -> None:
        """A broken-module loop is not ready, and the aggregator surfaces that."""
        loop = build_with_broken_module()
        agg = ReadinessAggregator()
        agg.register("loop", lambda: readiness(loop)["ready"])
        report = agg.evaluate()
        assert report["ready"] is False
        assert report["checks"]["loop"] == "fail"

    def test_aggregator_treats_a_raising_signal_as_not_ready(self) -> None:
        """A signal that raises is recorded as ``fail`` — the probe never crashes."""

        def boom() -> bool:
            raise RuntimeError("readiness probe blew up")

        agg = ReadinessAggregator()
        agg.register("ok", True)
        agg.register("flaky", boom)
        report = agg.evaluate()  # must NOT raise
        assert report["ready"] is False
        assert report["checks"]["flaky"] == "fail"
        assert "flaky" in report.get("errors", {})


# --------------------------------------------------- combined chaos still survives
def test_combined_faults_still_dispatch_and_stay_degraded() -> None:
    """Broken module + failing agent + degraded bus: the chain STILL dispatches.

    The worst realistic case: a missing module, a chaos agent raising on every
    message, AND the message bus failed over to its in-memory fallback. PRD Step
    10 says the field teams must still get their orders — assert they do, and that
    the system honestly reports its degraded state.
    """
    bus = degraded_kafka_bus()
    loop = build_with_broken_module(bus=bus)
    bad = inject_failing_agent(loop.bus, subscriptions=[Topic.IOT_TELEMETRY])

    orders = drive_to_dispatch(loop)

    assert bus.degraded is True
    assert BROKEN_MODULE_PATH in loop.degraded_modules
    assert bad.raised >= 1
    assert orders, "combined faults stopped the dispatch pipeline"
    # Last-known orders survive in history for replay (PRD Step 10).
    assert last_known_orders(loop.bus)
