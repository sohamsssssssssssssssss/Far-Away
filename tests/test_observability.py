"""Tests for the observability module (PRD Step 9/10).

Drive a handful of messages across topics through an in-memory bus into a wired
:class:`MetricsCollector`, then assert the tallies, the Prometheus exposition
text format, and that :func:`health` reflects a fully built system. Stdlib only;
no network, broker, solver or ML dependency.
"""
from __future__ import annotations

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.observability import (
    MetricsCollector,
    all_topics,
    build_agents,
    health,
    render,
)
from disastermind.observability.collector import MetricsCollector as DirectCollector
from disastermind.orchestration.loop import build_system


def _wire_collector() -> tuple[InMemoryBus, MetricsCollector]:
    bus = InMemoryBus()
    logger = DecisionLogger.null()
    collector = MetricsCollector(bus, logger)
    return bus, collector


def _publish_sample(bus: InMemoryBus) -> None:
    """Publish a representative spread of messages across topics/types/priorities."""
    msgs = [
        Message(
            sender="ingest.usgs", recipient="broadcast", type=MessageType.ALERT,
            priority=Priority.CRITICAL, topic=Topic.RAW_FEED, module=Module.EARTHQUAKE,
        ),
        Message(
            sender="prediction.eq", recipient="broadcast", type=MessageType.ALERT,
            priority=Priority.HIGH, topic=Topic.PREDICTION, module=Module.EARTHQUAKE,
        ),
        Message(
            sender="resource", recipient="commander", type=MessageType.INSTRUCTION,
            priority=Priority.HIGH, topic=Topic.RESOURCE_PLAN, module=Module.EARTHQUAKE,
        ),
        Message(
            sender="commander", recipient="dispatch", type=MessageType.INSTRUCTION,
            priority=Priority.CRITICAL, topic=Topic.DISPATCH, module=Module.EARTHQUAKE,
            payload={"kind": "dispatch", "channel": "sms"},
        ),
        # A dispatch ACK receipt — must NOT count as a real dispatch order.
        Message(
            sender="dispatch.router", recipient="commander", type=MessageType.ACK,
            priority=Priority.LOW, topic=Topic.DISPATCH, module=Module.EARTHQUAKE,
            payload={"kind": "dispatch_ack", "delivered": 1},
        ),
        # An escalation carrying a trigger.
        Message(
            sender="commander", recipient="human", type=MessageType.ESCALATION,
            priority=Priority.CRITICAL, topic=Topic.ESCALATION, module=Module.EARTHQUAKE,
            escalation_trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
        ),
    ]
    for m in msgs:
        bus.publish(m)


# --------------------------------------------------------------------- counts
def test_collector_subscribes_to_all_topics():
    topics = all_topics()
    # Every well-known Topic constant must be present.
    assert Topic.RAW_FEED in topics
    assert Topic.DISPATCH in topics
    assert Topic.ESCALATION in topics
    bus, collector = _wire_collector()
    assert set(collector.subscriptions) == set(topics)
    assert collector.decision_authority is False


def test_collector_counts_per_topic_type_priority():
    bus, collector = _wire_collector()
    _publish_sample(bus)

    assert collector.total == 6
    assert collector.by_topic[Topic.RAW_FEED] == 1
    assert collector.by_topic[Topic.DISPATCH] == 2  # order + ack both observed
    assert collector.by_type[MessageType.ALERT.value] == 2
    assert collector.by_type[MessageType.INSTRUCTION.value] == 2
    assert collector.by_priority[int(Priority.CRITICAL)] == 3
    assert collector.by_priority[int(Priority.HIGH)] == 2


def test_collector_escalation_and_dispatch_tallies():
    bus, collector = _wire_collector()
    _publish_sample(bus)

    # One escalation message carrying CROSS_STATE_RESOURCE.
    assert collector.escalations == 1
    assert collector.by_trigger[EscalationTrigger.CROSS_STATE_RESOURCE.value] == 1
    # Exactly one *real* dispatch order; the dispatch_ack receipt is excluded.
    assert collector.dispatches == 1


def test_collector_emits_nothing():
    bus, collector = _wire_collector()
    before = len(bus.history)
    _publish_sample(bus)
    # History grows only by the messages WE published — collector never emits.
    assert len(bus.history) == before + 6


# ---------------------------------------------------------------- exposition
def test_exposition_text_format():
    bus, collector = _wire_collector()
    _publish_sample(bus)
    text = render(collector)

    assert isinstance(text, str)
    assert text.endswith("\n")
    # Prometheus families: HELP + TYPE header lines present.
    assert "# HELP disastermind_messages_total" in text
    assert "# TYPE disastermind_messages_total counter" in text
    assert "disastermind_messages_total 6" in text
    # Labelled samples.
    assert f'disastermind_messages_by_topic_total{{topic="{Topic.DISPATCH}"}} 2' in text
    assert 'disastermind_messages_by_type_total{type="alert"} 2' in text
    assert 'disastermind_messages_by_priority_total{priority="1"} 3' in text
    assert "disastermind_escalations_total 1" in text
    assert (
        f'disastermind_escalations_by_trigger_total{{trigger="'
        f'{EscalationTrigger.CROSS_STATE_RESOURCE.value}"}} 1'
    ) in text
    assert "disastermind_dispatches_total 1" in text
    assert "# TYPE disastermind_collector_uptime_seconds gauge" in text

    # Every non-comment, non-blank line must be a valid "name ... value" sample.
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(" ", 1)
        assert len(parts) == 2, line
        float(parts[1])  # value must parse as a number


def test_snapshot_is_serialisable_dict():
    bus, collector = _wire_collector()
    _publish_sample(bus)
    snap = collector.snapshot()
    import json

    json.dumps(snap)  # must not raise
    assert snap["total"] == 6
    assert snap["dispatches"] == 1
    assert snap["escalations"] == 1


# -------------------------------------------------------------------- health
def test_health_reflects_built_system():
    loop = build_system()
    report = health(loop)

    assert report["status"] in ("ok", "degraded")
    assert report["agent_count"] == len(loop.agents)
    assert report["agent_count"] > 0
    assert report["degraded_modules"] == loop.degraded_modules
    assert "components" in report and len(report["components"]) == report["agent_count"]
    assert report["bus"]["type"] == "InMemoryBus"
    assert report["bus"]["degraded"] is False


def test_health_marks_degraded_when_modules_failed():
    loop = build_system()
    loop.degraded_modules = ["disastermind.tier2.prediction.build"]
    report = health(loop)
    assert report["status"] == "degraded"
    assert "disastermind.tier2.prediction.build" in report["degraded_modules"]


# ---------------------------------------------------------------- build.py
def test_build_agents_contract():
    bus = InMemoryBus()
    logger = DecisionLogger.null()
    settings = Settings()
    agents = build_agents(bus, logger, settings)
    assert len(agents) == 1
    assert isinstance(agents[0], DirectCollector)
    assert agents[0].decision_authority is False
