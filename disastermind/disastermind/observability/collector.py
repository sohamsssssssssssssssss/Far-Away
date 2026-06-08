"""MetricsCollector — passive observability agent (PRD Step 9/10 monitoring).

A Tier-3, zero-authority agent that subscribes to **every** well-known
:class:`~disastermind.core.contracts.Topic` and tallies the message stream
without ever altering or originating a decision. It is the read-only telemetry
plane behind the Prometheus exposition and the :func:`health` probe used by the
operations dashboard (PRD Step 9 decision logging / Step 10 graceful degradation
monitoring).

What it counts (all monotonically increasing):
  * total messages observed,
  * messages per ``topic``,
  * messages per :class:`~disastermind.core.contracts.MessageType`,
  * messages per :class:`~disastermind.core.contracts.Priority`,
  * escalation tally (messages of type ESCALATION or carrying an
    ``escalation_trigger``), broken down per trigger,
  * dispatch tally (real DISPATCH orders on ``Topic.DISPATCH``, excluding the
    router's housekeeping ``dispatch_ack`` receipts).

Like all Tier-3 agents it has ``decision_authority = False`` and emits nothing,
so wiring it into the DAG can never perturb the load-bearing chain.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.contracts import Message, MessageType, Tier, Topic

log = logging.getLogger("disastermind.observability.collector")


def all_topics() -> list[str]:
    """Return every public ``Topic.*`` string constant (introspected, not hard-coded).

    Introspecting :class:`~disastermind.core.contracts.Topic` keeps the collector
    correct if the foundation grows a topic — we never restate the list here.
    """
    return [
        getattr(Topic, name)
        for name in vars(Topic)
        if not name.startswith("_") and isinstance(getattr(Topic, name), str)
    ]


class MetricsCollector(BaseAgent):
    """Subscribes to all topics and tallies the message stream (read-only)."""

    tier = Tier.EDGE
    decision_authority = False  # observability never decides or emits.

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        name: str = "observability.metrics",
    ) -> None:
        self.started_at = __import__("time").time()
        self.total: int = 0
        self.by_topic: dict[str, int] = defaultdict(int)
        self.by_type: dict[str, int] = defaultdict(int)
        self.by_priority: dict[int, int] = defaultdict(int)
        self.escalations: int = 0
        self.by_trigger: dict[str, int] = defaultdict(int)
        self.dispatches: int = 0
        super().__init__(
            name=name, bus=bus, logger=logger, subscriptions=all_topics()
        )

    # ------------------------------------------------------------------ hooks
    def handle(self, message: Message) -> list[Message]:
        """Tally one observed message; return nothing (read-only, Step 10)."""
        self.total += 1
        self.by_topic[message.topic] += 1
        self.by_type[message.type.value] += 1
        self.by_priority[int(message.priority)] += 1

        payload = message.payload or {}
        if message.type is MessageType.ESCALATION or message.escalation_trigger is not None:
            self.escalations += 1
            if message.escalation_trigger is not None:
                self.by_trigger[message.escalation_trigger.value] += 1

        # Count real dispatch ORDERS only — skip the router's ACK receipts so the
        # dispatch tally tracks decisions executed, not housekeeping chatter.
        if message.topic == Topic.DISPATCH:
            if message.type is not MessageType.ACK and payload.get("kind") != "dispatch_ack":
                self.dispatches += 1

        return []

    # --------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        """Return a plain-dict snapshot of all counters (JSON-serialisable)."""
        return {
            "collector": self.name,
            "uptime_seconds": max(0.0, __import__("time").time() - self.started_at),
            "total": self.total,
            "by_topic": dict(self.by_topic),
            "by_type": dict(self.by_type),
            "by_priority": {str(k): v for k, v in self.by_priority.items()},
            "escalations": self.escalations,
            "by_trigger": dict(self.by_trigger),
            "dispatches": self.dispatches,
        }
