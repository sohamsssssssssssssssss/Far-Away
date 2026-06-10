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
from typing import Callable

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.contracts import Message, MessageType, Tier, Topic

log = logging.getLogger("disastermind.observability.collector")

#: A clock returns a comparable, monotone "tick". In production a caller may
#: inject ``time.monotonic``; in tests we use a deterministic integer counter so
#: latency assertions never touch the wall clock (PRD HARD RULE 2).
Clock = Callable[[], float]

#: Default Prometheus-style histogram bucket upper-bounds (``le``), expressed in
#: the same unit as the injected clock's tick delta (logical ticks in tests,
#: seconds in production). Monotonically increasing; ``+Inf`` is appended by the
#: exposition renderer. Chosen to span sub-tick gaps through multi-step chains.
DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
)


class _Histogram:
    """A minimal cumulative-bucket latency histogram (Prometheus semantics).

    Observations are bucketed into the configured upper-bounds (``le``); the
    exposition renders *cumulative* bucket counts plus ``_sum`` and ``_count``.
    Stdlib-only, no client library. The observed value is a clock-tick *delta*
    (logical in tests, seconds in prod) — never a raw wall-clock reading.
    """

    __slots__ = ("buckets", "_counts", "sum", "count")

    def __init__(self, buckets: tuple[float, ...] = DEFAULT_LATENCY_BUCKETS) -> None:
        self.buckets = tuple(sorted(buckets))
        # Per-bucket (non-cumulative) counts, indexed parallel to self.buckets,
        # plus one trailing slot for the implicit +Inf bucket.
        self._counts: list[int] = [0] * (len(self.buckets) + 1)
        self.sum: float = 0.0
        self.count: int = 0

    def observe(self, value: float) -> None:
        """Record one latency observation (value >= 0)."""
        v = float(value)
        self.sum += v
        self.count += 1
        for i, edge in enumerate(self.buckets):
            if v <= edge:
                self._counts[i] += 1
                return
        self._counts[-1] += 1  # +Inf bucket

    def cumulative(self) -> list[tuple[float, int]]:
        """Return ``[(le, cumulative_count), ...]`` including the ``+Inf`` bucket.

        The last tuple uses ``float('inf')`` as the upper bound, whose cumulative
        count equals ``count`` (Prometheus invariant).
        """
        out: list[tuple[float, int]] = []
        running = 0
        for i, edge in enumerate(self.buckets):
            running += self._counts[i]
            out.append((edge, running))
        running += self._counts[-1]
        out.append((float("inf"), running))
        return out

    def to_dict(self) -> dict:
        """JSON-serialisable view (``+Inf`` rendered as the string ``"+Inf"``)."""
        return {
            "buckets": [
                {"le": ("+Inf" if edge == float("inf") else edge), "count": c}
                for edge, c in self.cumulative()
            ],
            "sum": self.sum,
            "count": self.count,
        }


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
        *,
        clock: Clock | None = None,
        latency_buckets: tuple[float, ...] = DEFAULT_LATENCY_BUCKETS,
    ) -> None:
        self.started_at = __import__("time").time()
        self.total: int = 0
        self.by_topic: dict[str, int] = defaultdict(int)
        self.by_type: dict[str, int] = defaultdict(int)
        self.by_priority: dict[int, int] = defaultdict(int)
        self.escalations: int = 0
        self.by_trigger: dict[str, int] = defaultdict(int)
        self.dispatches: int = 0

        # --- latency + error instrumentation (additive) ----------------------
        # Logical clock: each observed message reads a monotone tick. The
        # default counter is wall-clock-free so latency is deterministic in
        # tests; production may inject ``time.monotonic``.
        self._tick: float = 0.0
        self.clock: Clock = clock or self._default_clock
        self._latency_buckets = tuple(sorted(latency_buckets))
        #: Per-topic processing-latency histogram (tick delta between the
        #: previous and current observation *of the same topic*).
        self.latency_by_topic: dict[str, _Histogram] = {}
        #: Tick at which each topic was last observed (for the inter-arrival
        #: latency derivation); keyed by topic.
        self._last_tick_by_topic: dict[str, float] = {}
        #: Error/failure counters, broken down by ``kind`` (e.g. a payload
        #: ``error``/``failure`` flag, a ``degraded`` marker, or status="error").
        self.errors: int = 0
        self.by_error_kind: dict[str, int] = defaultdict(int)

        super().__init__(
            name=name, bus=bus, logger=logger, subscriptions=all_topics()
        )

    # --------------------------------------------------------------- clock
    def _default_clock(self) -> float:
        """Monotone integer tick (no wall-clock dependency, PRD HARD RULE 2)."""
        self._tick += 1.0
        return self._tick

    def _histogram_for(self, topic: str) -> _Histogram:
        hist = self.latency_by_topic.get(topic)
        if hist is None:
            hist = _Histogram(self._latency_buckets)
            self.latency_by_topic[topic] = hist
        return hist

    # ------------------------------------------------------------------ hooks
    def handle(self, message: Message) -> list[Message]:
        """Tally one observed message; return nothing (read-only, Step 10)."""
        tick = self.clock()
        self.total += 1
        self.by_topic[message.topic] += 1
        self.by_type[message.type.value] += 1
        self.by_priority[int(message.priority)] += 1

        # Per-topic processing latency: the tick delta since the previous
        # message *on this topic* — a logical inter-arrival time derived from
        # message ordering / the injected clock, never wall-clock in tests.
        # Honour an explicit per-message latency hint if a producer supplies one
        # (``payload["latency"]`` / ``payload["processing_seconds"]``), so the
        # histogram reflects real upstream timings when available.
        payload = message.payload or {}
        latency = self._explicit_latency(payload)
        if latency is None:
            previous = self._last_tick_by_topic.get(message.topic)
            latency = (tick - previous) if previous is not None else 0.0
        if latency >= 0:
            self._histogram_for(message.topic).observe(latency)
        self._last_tick_by_topic[message.topic] = tick

        if message.type is MessageType.ESCALATION or message.escalation_trigger is not None:
            self.escalations += 1
            if message.escalation_trigger is not None:
                self.by_trigger[message.escalation_trigger.value] += 1

        # Count real dispatch ORDERS only — skip the router's ACK receipts so the
        # dispatch tally tracks decisions executed, not housekeeping chatter.
        if message.topic == Topic.DISPATCH:
            if message.type is not MessageType.ACK and payload.get("kind") != "dispatch_ack":
                self.dispatches += 1

        # Error/failure counter: a message is "in error" if it carries an
        # explicit failure marker (a truthy ``error``/``failed`` payload flag, a
        # ``status``/``kind`` of error, or a ``degraded`` marker). Purely
        # additive — normal traffic never increments it.
        kind = self._error_kind(message, payload)
        if kind is not None:
            self.errors += 1
            self.by_error_kind[kind] += 1

        return []

    @staticmethod
    def _explicit_latency(payload: dict) -> float | None:
        """Extract a producer-supplied latency hint, if present and numeric."""
        for key in ("latency", "latency_seconds", "processing_seconds", "duration"):
            if key in payload:
                try:
                    value = float(payload[key])
                except (TypeError, ValueError):
                    return None
                return value if value >= 0 else None
        return None

    @staticmethod
    def _error_kind(message: Message, payload: dict) -> str | None:
        """Classify a message as an error observation, or ``None`` if healthy.

        Recognises common conventions used across the codebase without coupling
        to any single producer: a truthy ``error``/``failed`` flag, a
        ``status``/``kind`` of ``"error"``/``"failure"``, or a ``degraded`` flag.
        Returns a short, label-safe ``kind`` string for the per-kind counter.
        """
        status = str(payload.get("status", "")).lower()
        kind_field = str(payload.get("kind", "")).lower()
        if status in ("error", "failed", "failure"):
            return status if status != "failed" else "failure"
        if kind_field in ("error", "failure"):
            return kind_field
        if payload.get("error") or payload.get("failed"):
            return "error"
        if payload.get("degraded"):
            return "degraded"
        return None

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
            "errors": self.errors,
            "by_error_kind": dict(self.by_error_kind),
            "latency_by_topic": {
                topic: hist.to_dict()
                for topic, hist in sorted(self.latency_by_topic.items())
            },
        }
