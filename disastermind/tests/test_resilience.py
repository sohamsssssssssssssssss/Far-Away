"""Resilience / graceful-degradation harness tests (PRD Step 10).

Proves the four graceful-degradation claims the foundation makes, using the
reusable helpers in :mod:`disastermind.resilience`:

  (a) an agent that raises in ``handle()`` / ``tick()`` does NOT stop the loop or
      its sibling agents — the pipeline still reaches ``Topic.DISPATCH``;
  (b) ``build_system`` with a deliberately-broken module path records it in
      ``loop.degraded_modules`` yet still reaches ``Topic.DISPATCH``;
  (c) ``KafkaBus`` pointed at an unreachable broker reports ``degraded=True`` and
      transparently falls back to an in-memory bus (lazy-import/connection
      fallback only — NEVER a real broker);
  (d) last-known-state survival: after the bus stops delivering, previously
      produced orders remain in history.

Stdlib-only, fully offline, deterministic (PRD HARD RULE 2 / Step 10).
"""
from __future__ import annotations

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus, KafkaBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from disastermind.orchestration.loop import MODULE_BUILD_PATHS, build_system
from disastermind.resilience import (
    BROKEN_MODULE_PATH,
    FailingAgent,
    FrozenBus,
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
    logger = DecisionLogger.null()
    settings = Settings()
    coord = build_system(bus=bus, logger=logger, settings=settings)
    return coord


# --------------------------------------------------------------------------- (a)
class TestAgentIsolation:
    """A failing agent must not stop the loop or its siblings (PRD Step 10)."""

    def test_failing_agent_is_zero_authority_tier3(self):
        """The chaos probe is a real, harmless (zero-authority) Tier-3 agent."""
        agent = FailingAgent(bus=InMemoryBus())
        assert agent.tier is Tier.EDGE
        assert agent.decision_authority is False

    def test_failing_agent_handle_and_tick_raise(self):
        """Both reactive and periodic entry points deliberately raise."""
        agent = FailingAgent(bus=InMemoryBus())
        with pytest.raises(RuntimeError):
            agent.handle(
                Message(
                    sender="t",
                    recipient="x",
                    type=MessageType.QUERY,
                    priority=Priority.INFO,
                    topic=Topic.IOT_TELEMETRY,
                )
            )
        with pytest.raises(RuntimeError):
            agent.tick()

    def test_failing_agent_does_not_break_pipeline(self, system):
        """Inject a failing agent; the chain still reaches a real DISPATCH."""
        failing = inject_failing_agent(system.bus, system.logger)
        orders = drive_to_dispatch(system)

        # The failure path was genuinely exercised (the seed frame hit handle()).
        assert failing.raised > 0, "failing agent was never invoked"
        # ...yet the load-bearing chain still produced real DISPATCH orders.
        assert orders, "a failing sibling agent broke the pipeline (Step 10 violated)"

    def test_failing_agents_siblings_still_emit_full_chain(self, system):
        """Every load-bearing topic still fires despite the chaos agent."""
        inject_failing_agent(system.bus, system.logger)
        drive_to_dispatch(system)

        counts: dict[str, int] = {}
        for m in system.bus.history:
            counts[m.topic] = counts.get(m.topic, 0) + 1
        for topic in (
            Topic.RAW_FEED,
            Topic.PREDICTION,
            Topic.CASCADE,
            Topic.RESOURCE_PLAN,
            Topic.ROUTING_PLAN,
            Topic.FIELD_ORDER,
            Topic.DISPATCH,
        ):
            assert counts.get(topic, 0) > 0, f"{topic} did not fire with a failing sibling"

    def test_loop_run_once_survives_failing_tick(self, system):
        """A tick() that raises must not stop the coordination loop."""
        failing = inject_failing_agent(system.bus, system.logger)
        seed_field_teams(system.bus)

        before = failing.raised
        # run_once drives every agent's tick(); the failing one raises but the
        # loop must complete and advance its cycle counter regardless.
        cycle = system.run_once(now_epoch=0.0)
        assert cycle == 1
        assert failing.raised > before, "failing tick() was not exercised by run_once"

        # A second cycle still advances — the loop did not die.
        assert system.run_once(now_epoch=1.0) == 2

    def test_inmemory_bus_publish_isolates_subscriber_exception(self):
        """InMemoryBus.publish must not propagate a subscriber's exception."""
        bus = InMemoryBus()
        delivered: list[Message] = []
        FailingAgent(bus=bus, subscriptions=[Topic.DISPATCH])
        bus.subscribe(Topic.DISPATCH, "good", lambda m: delivered.append(m))

        msg = Message(
            sender="x",
            recipient="y",
            type=MessageType.INSTRUCTION,
            priority=Priority.HIGH,
            topic=Topic.DISPATCH,
        )
        # Must not raise even though one subscriber blows up...
        bus.publish(msg)
        # ...and the healthy subscriber still received the message.
        assert delivered == [msg]


# --------------------------------------------------------------------------- (b)
class TestModuleIsolation:
    """A broken module is recorded + skipped but the DAG still dispatches."""

    def test_broken_module_lands_in_degraded_modules(self):
        coord = build_with_broken_module()
        assert BROKEN_MODULE_PATH in coord.degraded_modules

    def test_build_with_broken_module_restores_global_paths(self):
        """The monkeypatch-style injection must not leak global state."""
        original = list(MODULE_BUILD_PATHS)
        build_with_broken_module()
        assert list(MODULE_BUILD_PATHS) == original, "MODULE_BUILD_PATHS left mutated"
        assert BROKEN_MODULE_PATH not in MODULE_BUILD_PATHS

    def test_broken_module_still_reaches_dispatch(self):
        coord = build_with_broken_module()
        orders = drive_to_dispatch(coord)
        assert orders, "broken module aborted the pipeline (Step 10 violated)"

    def test_healthy_modules_still_wire_alongside_broken_one(self):
        """All real modules load; only the injected bad path is degraded."""
        coord = build_with_broken_module()
        assert coord.agents, "no agents wired despite only one broken module"
        assert coord.degraded_modules == [BROKEN_MODULE_PATH]

    def test_build_with_broken_module_accepts_injected_bus(self):
        """The helper threads an explicit bus/logger/settings through."""
        bus = InMemoryBus()
        coord = build_with_broken_module(bus=bus, logger=DecisionLogger.null())
        assert coord.bus is bus


# --------------------------------------------------------------------------- (c)
class TestBusFailover:
    """KafkaBus degrades to in-memory when the broker is unreachable."""

    def test_unreachable_broker_reports_degraded(self):
        bus = degraded_kafka_bus()
        assert isinstance(bus, KafkaBus)
        assert bus.degraded is True
        # No real producer was created (we never touched a broker).
        assert bus._producer is None

    def test_degraded_kafka_falls_back_to_inmemory_delivery(self):
        """Publish/subscribe still work via the in-memory fallback."""
        bus = degraded_kafka_bus()
        received: list[Message] = []
        bus.subscribe(Topic.DISPATCH, "probe", lambda m: received.append(m))
        msg = Message(
            sender="x",
            recipient="y",
            type=MessageType.INSTRUCTION,
            priority=Priority.HIGH,
            topic=Topic.DISPATCH,
        )
        bus.publish(msg)
        assert received == [msg], "degraded KafkaBus did not deliver via fallback"

    def test_full_system_runs_on_degraded_kafka_bus(self):
        """The whole DAG dispatches even when wired on a degraded KafkaBus."""
        bus = degraded_kafka_bus()
        coord = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
        orders = drive_to_dispatch(coord)
        assert bus.degraded is True
        assert orders, "degraded KafkaBus failover did not reach DISPATCH"

    def test_kafka_uses_invalid_tld_never_real_broker(self):
        """Sanity: the helper targets RFC-6761 .invalid hosts, not real brokers."""
        bus = degraded_kafka_bus()
        assert bus.brokers.endswith(".invalid:9092")
        assert bus.backup_brokers is not None and bus.backup_brokers.endswith(".invalid:9092")


# --------------------------------------------------------------------------- (d)
class TestLastKnownState:
    """Previously-produced orders survive once the bus stops delivering."""

    def test_orders_survive_after_freeze(self):
        bus = FrozenBus()
        coord = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
        before = drive_to_dispatch(coord)
        assert before, "no orders produced before the simulated outage"

        bus.freeze()  # bus goes down — delivery suppressed, history retained
        bus.publish(
            Message(
                sender="late",
                recipient="x",
                type=MessageType.INSTRUCTION,
                priority=Priority.CRITICAL,
                topic=Topic.DISPATCH,
                module=Module.ALL,
            )
        )
        after = last_known_orders(bus)
        assert len(after) == len(before), "last-known state changed after the outage"

    def test_frozen_bus_suppresses_delivery_but_keeps_history(self):
        bus = FrozenBus()
        seen: list[Message] = []
        bus.subscribe(Topic.DISPATCH, "probe", lambda m: seen.append(m))

        live = Message(
            sender="a",
            recipient="b",
            type=MessageType.INSTRUCTION,
            priority=Priority.HIGH,
            topic=Topic.DISPATCH,
        )
        bus.publish(live)
        assert seen == [live]
        history_len = len(bus.history)

        bus.freeze()
        bus.publish(
            Message(
                sender="c",
                recipient="d",
                type=MessageType.INSTRUCTION,
                priority=Priority.HIGH,
                topic=Topic.DISPATCH,
            )
        )
        # No new delivery and no new history after the outage.
        assert seen == [live], "FrozenBus delivered after the bus went down"
        assert len(bus.history) == history_len, "FrozenBus mutated last-known state"

    def test_last_on_returns_last_known_order(self):
        """last_on() still surfaces the most recent pre-outage order."""
        bus = FrozenBus()
        coord = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
        drive_to_dispatch(coord)
        bus.freeze()
        last = bus.last_on(Topic.DISPATCH)
        assert last is not None, "no last-known DISPATCH retained after outage"
