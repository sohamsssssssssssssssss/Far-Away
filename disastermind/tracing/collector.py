"""TraceCollector — incident-correlating trace agent (PRD Step 9/10).

A Tier-3, zero-authority agent that subscribes to **every** well-known
:class:`~disastermind.core.contracts.Topic` and reconstructs the lifecycle of
each incident from the message stream. Unlike the observability
:class:`~disastermind.observability.collector.MetricsCollector` (global tallies),
this agent *correlates by ``incident_id``*: it records the ordered sequence of
topics each incident traversed and derives a span/latency snapshot per incident.

Why "latency" without wall clock (PRD HARD RULE 2 — deterministic, no real-time
assertions): the synchronous in-memory bus fans messages out in publish order,
so the *position* of a message in the observed stream is a stable, monotone
logical timestamp. :meth:`incident_latency` therefore measures the number of
observed messages between an incident's first :data:`Topic.RAW_FEED` and its last
:data:`Topic.DISPATCH` order — a logical latency that is identical on every run,
independent of the machine clock.

Like all Tier-3 agents it has ``decision_authority = False`` and emits nothing,
so wiring it into the DAG can never perturb the load-bearing chain (PRD Step 2).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.contracts import Message, MessageType, Tier, Topic

log = logging.getLogger("disastermind.tracing.collector")


def all_topics() -> list[str]:
    """Return every public ``Topic.*`` string constant (introspected).

    Introspecting :class:`~disastermind.core.contracts.Topic` keeps the collector
    correct if the foundation grows a topic — we never restate the list here
    (mirrors :func:`disastermind.observability.collector.all_topics`).
    """
    return [
        getattr(Topic, name)
        for name in vars(Topic)
        if not name.startswith("_") and isinstance(getattr(Topic, name), str)
    ]


def _is_real_dispatch(message: Message) -> bool:
    """A genuine DISPATCH *order*, not the router's housekeeping ACK receipt.

    Matches the convention used across the codebase (MetricsCollector,
    scenarios) so latency is measured to the real terminal dispatch, not chatter.
    """
    if message.topic != Topic.DISPATCH:
        return False
    if message.type is MessageType.ACK:
        return False
    return (message.payload or {}).get("kind") != "dispatch_ack"


@dataclass
class _IncidentTrace:
    """Accumulated per-incident view (internal; surfaced via snapshots)."""

    incident_id: str
    #: (sequence_index, topic) in observation order — sequence_index is the
    #: collector-wide logical clock at the moment the message was seen.
    steps: list[tuple[int, str]] = field(default_factory=list)
    by_topic: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    first_raw_feed_seq: int | None = None
    last_dispatch_seq: int | None = None
    modules: set[str] = field(default_factory=set)

    @property
    def span_count(self) -> int:
        """Number of observed messages (logical spans) for this incident."""
        return len(self.steps)

    @property
    def topics(self) -> list[str]:
        """Distinct topics traversed, in first-seen order."""
        seen: list[str] = []
        for _, topic in self.steps:
            if topic not in seen:
                seen.append(topic)
        return seen

    @property
    def latency(self) -> int | None:
        """Logical latency: last-DISPATCH seq minus first-RAW_FEED seq.

        ``None`` until the incident has both a RAW_FEED and a real DISPATCH —
        i.e. it has not yet completed the load-bearing chain.
        """
        if self.first_raw_feed_seq is None or self.last_dispatch_seq is None:
            return None
        return self.last_dispatch_seq - self.first_raw_feed_seq

    @property
    def reached_dispatch(self) -> bool:
        return self.last_dispatch_seq is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "span_count": self.span_count,
            "topics": self.topics,
            "by_topic": dict(self.by_topic),
            "modules": sorted(self.modules),
            "first_raw_feed_seq": self.first_raw_feed_seq,
            "last_dispatch_seq": self.last_dispatch_seq,
            "latency": self.latency,
            "reached_dispatch": self.reached_dispatch,
        }


class TraceCollector(BaseAgent):
    """Subscribes to all topics; correlates the stream by ``incident_id``."""

    tier = Tier.EDGE
    decision_authority = False  # tracing never decides or emits.

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        name: str = "tracing.collector",
    ) -> None:
        # Collector-wide logical clock: every observed message increments it,
        # giving a deterministic ordering independent of wall time (Step 10).
        self._seq: int = 0
        self.total: int = 0
        self.by_topic: dict[str, int] = defaultdict(int)
        self.incidents: dict[str, _IncidentTrace] = {}
        #: Messages observed with no incident_id (cannot be correlated).
        self.uncorrelated: int = 0
        super().__init__(name=name, bus=bus, logger=logger, subscriptions=all_topics())

    # ------------------------------------------------------------------ hooks
    def handle(self, message: Message) -> list[Message]:
        """Tally + correlate one observed message; emit nothing (Step 10)."""
        self._seq += 1
        self.total += 1
        self.by_topic[message.topic] += 1

        incident_id = message.incident_id
        if not incident_id:
            self.uncorrelated += 1
            return []

        trace = self.incidents.get(incident_id)
        if trace is None:
            trace = _IncidentTrace(incident_id=incident_id)
            self.incidents[incident_id] = trace

        trace.steps.append((self._seq, message.topic))
        trace.by_topic[message.topic] += 1
        if getattr(message.module, "value", None) is not None:
            trace.modules.add(message.module.value)

        # First RAW_FEED anchors the start of the chain; last real DISPATCH
        # anchors the end. Using message ordering (self._seq), never wall clock.
        if message.topic == Topic.RAW_FEED and trace.first_raw_feed_seq is None:
            trace.first_raw_feed_seq = self._seq
        if _is_real_dispatch(message):
            trace.last_dispatch_seq = self._seq

        return []

    # --------------------------------------------------------------- queries
    def incident_latency(self, incident_id: str) -> int | None:
        """Logical latency (RAW_FEED -> last DISPATCH) for one incident.

        Returns ``None`` for an unknown incident or one that has not yet reached
        a real DISPATCH. The unit is "messages observed between the two anchors"
        — a deterministic logical clock, never wall-clock seconds (PRD Step 10).
        """
        trace = self.incidents.get(incident_id)
        return trace.latency if trace is not None else None

    def incident_topics(self, incident_id: str) -> list[str]:
        """Distinct topics an incident traversed, in first-seen order."""
        trace = self.incidents.get(incident_id)
        return trace.topics if trace is not None else []

    def incident_snapshot(self, incident_id: str) -> dict[str, Any] | None:
        """Per-incident span/latency view, or ``None`` if unseen."""
        trace = self.incidents.get(incident_id)
        return trace.to_dict() if trace is not None else None

    def snapshot(self) -> dict[str, Any]:
        """Plain-dict snapshot of all counters + per-incident traces (JSON-able)."""
        return {
            "collector": self.name,
            "total": self.total,
            "incident_count": len(self.incidents),
            "uncorrelated": self.uncorrelated,
            "by_topic": dict(self.by_topic),
            "incidents": {
                iid: trace.to_dict() for iid, trace in self.incidents.items()
            },
        }
