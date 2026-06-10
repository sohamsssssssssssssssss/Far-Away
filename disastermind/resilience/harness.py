"""Reusable resilience helpers proving PRD Step 10 graceful degradation.

These helpers are deliberately small and dependency-free so they can be reused
by ``tests/test_resilience.py`` (and any future chaos harness) without pulling
in a broker, solver or ML stack. Nothing here touches the network: the Kafka
failover path exercises the *lazy-import / connection* fallback in
:class:`disastermind.core.bus.KafkaBus`, which degrades to an in-memory bus when
``confluent_kafka`` is absent (it is, in the stdlib-only test environment).

PRD Step 10: "If an agent fails, other agents continue independently; if the
message bus is down, agents operate on last-known state with auto-failover to a
backup cluster."
"""
from __future__ import annotations

import importlib
import logging
from typing import Callable

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import InMemoryBus, KafkaBus, MessageBus
from ..core.config import Settings
from ..core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)

log = logging.getLogger("disastermind.resilience")

#: A module path that is guaranteed never to import — used to prove that a broken
#: module is recorded in ``loop.degraded_modules`` without aborting boot. It is
#: intentionally absent from the codebase so the import always raises.
BROKEN_MODULE_PATH = "disastermind.resilience._deliberately_missing_module"

#: Sample pre-positioned teams (mirrors ``tests/conftest.py``) so a GPS-beacon
#: telemetry frame lets the field coordinator bind orders to real teams and the
#: chain reaches DISPATCH.
SAMPLE_TEAMS = [
    ("BOAT-01", "boat", 20.27, 85.84),
    ("BOAT-02", "boat", 20.35, 85.90),
    ("NDRF-01", "ndrf_team", 20.30, 85.82),
    ("SDRF-01", "sdrf_team", 20.25, 85.88),
    ("MED-01", "medical_unit", 20.29, 85.83),
    ("HELI-01", "helicopter", 20.24, 85.81),
    ("USAR-01", "usar_team", 20.31, 85.86),
    ("FIRE-01", "fire_engine", 20.28, 85.85),
]


# --------------------------------------------------------------------------- (a)
class FailingAgent(BaseAgent):
    """A chaos agent that ALWAYS raises (PRD Step 10 agent-isolation probe).

    It is a real Tier-3 (zero-authority) :class:`BaseAgent` so it subscribes to
    the bus exactly like a production agent. Both reactive (``handle``) and
    periodic (``tick``) entry points raise — proving that neither
    ``BaseAgent._on_message`` / ``run_tick`` nor ``InMemoryBus.publish`` lets a
    single faulty agent stop the loop or its siblings. ``raised`` counts the
    swallowed failures so tests can assert the failure path was actually hit.
    """

    tier: Tier = Tier.EDGE
    decision_authority: bool = False

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        name: str = "resilience.failing_agent",
        subscriptions: list[str] | None = None,
    ) -> None:
        # Subscribe to IoT telemetry by default: the test harness seeds field
        # teams on that topic, so this agent is guaranteed to be exercised.
        super().__init__(
            name=name,
            bus=bus,
            logger=logger,
            subscriptions=subscriptions if subscriptions is not None else [Topic.IOT_TELEMETRY],
        )
        self.raised = 0

    def handle(self, message: Message) -> list[Message]:
        self.raised += 1
        raise RuntimeError(f"{self.name}: deliberate handle() failure (chaos probe)")

    def tick(self) -> list[Message]:
        self.raised += 1
        raise RuntimeError(f"{self.name}: deliberate tick() failure (chaos probe)")


def inject_failing_agent(
    bus: MessageBus,
    logger: DecisionLogger | None = None,
    subscriptions: list[str] | None = None,
) -> FailingAgent:
    """Construct + subscribe a :class:`FailingAgent` onto ``bus``.

    Returns the agent so the caller can drive ``run_tick()`` and assert on
    ``agent.raised``. Subscribing it does not perturb existing agents — that is
    the whole point of the isolation guarantee (PRD Step 10).
    """
    return FailingAgent(bus=bus, logger=logger, subscriptions=subscriptions)


# --------------------------------------------------------------------------- (b)
def build_with_broken_module(
    bad_path: str = BROKEN_MODULE_PATH,
    bus: MessageBus | None = None,
    logger: DecisionLogger | None = None,
    settings: Settings | None = None,
):
    """Build the full system with a deliberately-broken module path appended.

    We must NOT edit ``orchestration/loop.py``. Instead we temporarily extend
    its module-level ``MODULE_BUILD_PATHS`` list (monkeypatch-style), call the
    real :func:`build_system`, then restore the list in a ``finally`` so global
    state is never left mutated. The broken path raises on import, so it lands
    in ``loop.degraded_modules`` while every healthy module still wires up — the
    DAG therefore still reaches ``Topic.DISPATCH`` (PRD Step 10).

    Returns the driven-ready :class:`CoordinationLoop`.
    """
    from ..orchestration import loop as loop_mod  # lazy: keeps import graph light

    original = list(loop_mod.MODULE_BUILD_PATHS)
    try:
        # Mutate in place so the constant the function reads is the one we extend.
        loop_mod.MODULE_BUILD_PATHS.append(bad_path)
        coord = loop_mod.build_system(bus=bus, logger=logger, settings=settings)
    finally:
        loop_mod.MODULE_BUILD_PATHS[:] = original
    return coord


# --------------------------------------------------------------------------- (c)
def degraded_kafka_bus(
    brokers: str = "broker-does-not-exist.invalid:9092",
    backup_brokers: str | None = "backup-does-not-exist.invalid:9092",
    client_id: str = "disastermind-resilience",
) -> KafkaBus:
    """Return a :class:`KafkaBus` that has degraded to its in-memory fallback.

    ``KafkaBus.__init__`` lazily imports ``confluent_kafka``; in the stdlib-only
    environment that import raises, so the constructor sets ``degraded=True`` and
    routes publish/subscribe through an :class:`InMemoryBus`. Even if the client
    *were* installed, the ``.invalid`` TLD (RFC 6761) guarantees the brokers are
    unreachable, so this NEVER contacts a real broker. The returned bus is fully
    usable: ``publish`` fans out to subscribers via the fallback.
    """
    return KafkaBus(brokers=brokers, backup_brokers=backup_brokers, client_id=client_id)


# --------------------------------------------------------------------------- (d)
class FrozenBus(MessageBus):
    """A bus wrapper that stops delivering new messages (simulated outage).

    Wraps a live :class:`InMemoryBus`. Before :meth:`freeze` it behaves exactly
    like the wrapped bus. After :meth:`freeze`, ``publish`` is a no-op for
    *delivery* but the already-accumulated ``history`` (the previously-produced
    orders) remains intact and queryable — modelling PRD Step 10's
    "agents operate on last-known state" guarantee when the bus goes down.
    """

    def __init__(self, inner: InMemoryBus | None = None) -> None:
        self.inner = inner or InMemoryBus()
        self.frozen = False

    @property
    def history(self) -> list[Message]:  # delegate so last-known state is visible
        return self.inner.history

    def freeze(self) -> None:
        """Simulate the bus going down: stop delivering, keep history."""
        self.frozen = True

    def publish(self, message: Message) -> None:
        if self.frozen:
            log.warning("FrozenBus: delivery suppressed (bus down) — last-known state retained")
            return
        self.inner.publish(message)

    def subscribe(self, topic: str, subscriber: str, callback: Callable[[Message], None]) -> None:
        self.inner.subscribe(topic, subscriber, callback)

    def last_on(self, topic: str) -> Message | None:
        return self.inner.last_on(topic)


# --------------------------------------------------------------------- driving
def seed_field_teams(bus: MessageBus, teams=SAMPLE_TEAMS) -> None:
    """Publish a GPS-beacon telemetry frame so the field tier tracks teams.

    Mirrors ``tests/conftest.py`` so the resilience harness drives the same
    load-bearing chain the verified e2e suite does.
    """
    readings = [
        {
            "team_id": tid,
            "asset_type": atype,
            "location": {"lat": lat, "lon": lon},
            "status": "idle",
        }
        for (tid, atype, lat, lon) in teams
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


def drive_to_dispatch(coord, teams=SAMPLE_TEAMS) -> list[Message]:
    """Seed teams + run every ingestion tick once, returning real DISPATCH orders.

    A "real" dispatch is an order, not a housekeeping ACK / delivery-ack — the
    same definition the e2e harness uses. Works against either a raw
    :class:`InMemoryBus` or the loop's bus.
    """
    seed_field_teams(coord.bus, teams)
    for a in coord.agents:
        if getattr(a, "name", "").startswith("ingest."):
            a.run_tick()
    return last_known_orders(coord.bus)


def bus_history(bus: MessageBus) -> list[Message]:
    """Return the effective message history of *any* bus flavour.

    The frozen base :class:`~disastermind.core.bus.KafkaBus` keeps its history
    only on a private ``_fallback`` :class:`~disastermind.core.bus.InMemoryBus`
    and exposes no public ``.history`` (it is, after all, a thin Kafka adapter,
    not a store). :class:`InMemoryBus` and :class:`FrozenBus` *do* expose a
    public ``.history``. We must NOT edit ``core/bus.py``, so this new-code
    helper bridges the contract: prefer a public ``.history`` when present,
    otherwise reach through a degraded KafkaBus's ``_fallback`` so dispatch that
    really happened on the in-memory fallback is visible to last-known-state
    queries (PRD Step 10).
    """
    history = getattr(bus, "history", None)
    if history is not None:
        return list(history)
    fallback = getattr(bus, "_fallback", None)
    if fallback is not None:
        return list(getattr(fallback, "history", []))
    return []


def last_known_orders(bus: MessageBus) -> list[Message]:
    """Real DISPATCH orders surviving in the bus history (last-known state).

    Filters out ACKs and dispatch-router delivery acknowledgements so only
    genuine orders remain — these are exactly the orders an agent would replay
    from last-known state after a bus outage (PRD Step 10).
    """
    out: list[Message] = []
    for m in bus_history(bus):
        if m.topic != Topic.DISPATCH:
            continue
        if m.type is MessageType.ACK:
            continue
        if (m.payload or {}).get("kind") == "dispatch_ack":
            continue
        out.append(m)
    return out
