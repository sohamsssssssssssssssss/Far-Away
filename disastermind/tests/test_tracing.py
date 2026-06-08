"""Tests for the tracing module (PRD Step 9/10 — decision tracing + correlation).

Three concerns, all stdlib-only and network-free (PRD HARD RULE 2):

  * **Spans** nest correctly and record start/end ticks from an *injected* clock
    (a monotone counter), so no assertion ever touches wall-clock time.
  * **TraceCollector** subscribes to every topic, tallies a *driven scenario*
    (the real earthquake pipeline) and returns a per-incident snapshot whose
    logical latency is derived from message ordering — deterministic across runs.
  * **OpenTelemetry export** is exercised only via a stub tracer; the real-SDK
    path is guarded with ``pytest.importorskip`` so the default test path needs
    no optional dependency and never opens a socket.
"""
from __future__ import annotations

import json

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from disastermind.tracing import (
    Span,
    SpanRecorder,
    TraceCollector,
    all_topics,
    build_agents,
    get_default_recorder,
    trace,
)


# --------------------------------------------------------------------------- #
# Injected clock helper                                                        #
# --------------------------------------------------------------------------- #
def _counter_clock():
    """A monotone integer clock: each call returns 1, 2, 3, ... as a float."""
    state = {"n": 0}

    def tick() -> float:
        state["n"] += 1
        return float(state["n"])

    return tick


# --------------------------------------------------------------------------- #
# Spans: recording + injected clock                                           #
# --------------------------------------------------------------------------- #
def test_span_records_start_end_and_duration_under_injected_clock():
    rec = SpanRecorder()
    clk = _counter_clock()  # 1 on open, 2 on close
    with trace("predict", recorder=rec, incident_id="EQ-1", clock=clk) as span:
        span.set("model", "xgboost")

    assert isinstance(span, Span)
    assert span.start == 1.0
    assert span.end == 2.0
    assert span.duration == 1.0
    assert span.is_closed is True
    assert span.status == "ok"
    assert span.incident_id == "EQ-1"
    assert span.attributes["model"] == "xgboost"
    # Recorded exactly once, as a root span.
    assert rec.spans == [span]
    assert rec.roots() == [span]
    assert span.parent_id is None


def test_spans_nest_and_form_a_tree_under_injected_clock():
    rec = SpanRecorder()
    clk = _counter_clock()
    # Share one clock across all spans so ticks are globally monotone:
    #   outer open=1, inner open=2, inner close=3, outer close=4
    with trace("coordinate", recorder=rec, incident_id="EQ-1", clock=clk) as outer:
        # While the outer span is open it is the current span on this thread.
        assert rec.current is outer
        with trace("allocate", recorder=rec, clock=clk) as inner:
            assert rec.current is inner
            # incident_id is inherited from the enclosing span (correlation).
            assert inner.incident_id == "EQ-1"
            # Parent linkage records the enclosing span's id.
            assert inner.parent_id == outer.span_id
        # After the inner span closes the outer is current again.
        assert rec.current is outer
    assert rec.current is None  # stack fully unwound

    assert outer.start == 1.0 and inner.start == 2.0
    assert inner.end == 3.0 and outer.end == 4.0
    # Tree shape: one root, one child.
    assert rec.roots() == [outer]
    assert rec.children_of(outer.span_id) == [inner]
    assert rec.children_of(inner.span_id) == []
    # by_incident correlates both spans to the same incident.
    assert set(s.span_id for s in rec.by_incident("EQ-1")) == {
        outer.span_id,
        inner.span_id,
    }


def test_trace_decorator_opens_a_fresh_span_per_call():
    rec = SpanRecorder()

    @trace("forecast", recorder=rec, incident_id="EQ-9")
    def forecast(x):
        return x * 2

    assert forecast(3) == 6
    assert forecast(4) == 8
    # Two calls -> two recorded, closed spans, both correlated to the incident.
    assert len(rec.spans) == 2
    for s in rec.spans:
        assert s.name == "forecast"
        assert s.incident_id == "EQ-9"
        assert s.is_closed is True
        assert s.parent_id is None  # each decorator call is a fresh root


def test_span_error_status_is_recorded_and_exception_propagates():
    rec = SpanRecorder()
    clk = _counter_clock()
    with pytest.raises(ValueError):
        with trace("risky", recorder=rec, clock=clk) as span:
            raise ValueError("boom")

    assert span.status == "error"
    assert span.is_closed is True  # span still closed despite the error
    assert span.attributes.get("error_type") == "ValueError"
    # The per-thread stack is unwound even when the body raised.
    assert rec.current is None


def test_recorder_snapshot_is_json_serialisable_and_reset_clears():
    rec = SpanRecorder()
    clk = _counter_clock()
    with trace("a", recorder=rec, incident_id="I-1", clock=clk):
        pass
    snap = rec.snapshot()
    json.dumps(snap)  # must not raise
    assert len(snap) == 1
    assert snap[0]["name"] == "a"
    assert snap[0]["incident_id"] == "I-1"
    assert snap[0]["duration"] == 1.0

    rec.reset()
    assert rec.spans == []
    assert rec.current is None


def test_default_recorder_is_process_wide_singleton():
    assert get_default_recorder() is get_default_recorder()


# --------------------------------------------------------------------------- #
# TraceCollector: subscriptions + driven-scenario correlation                 #
# --------------------------------------------------------------------------- #
def test_collector_subscribes_to_all_topics_and_has_no_authority():
    bus = InMemoryBus()
    collector = TraceCollector(bus, DecisionLogger.null())

    topics = all_topics()
    assert Topic.RAW_FEED in topics
    assert Topic.DISPATCH in topics
    assert Topic.ESCALATION in topics
    assert set(collector.subscriptions) == set(topics)
    # Tier-3, zero authority — wiring it in can never perturb the chain.
    assert collector.decision_authority is False
    assert collector.tier == Tier.EDGE


def test_build_agents_returns_single_zero_authority_collector():
    from disastermind.core.config import Settings

    bus = InMemoryBus()
    agents = build_agents(bus, DecisionLogger.null(), Settings())
    assert len(agents) == 1
    assert isinstance(agents[0], TraceCollector)
    assert agents[0].decision_authority is False


def test_collector_emits_nothing():
    bus = InMemoryBus()
    collector = TraceCollector(bus, DecisionLogger.null())
    before = len(bus.history)
    bus.publish(
        Message(
            sender="ingest", recipient="b", type=MessageType.ALERT,
            priority=Priority.CRITICAL, topic=Topic.RAW_FEED,
            module=Module.EARTHQUAKE, incident_id="EQ-X",
        )
    )
    # History grew only by the one message we published — collector never emits.
    assert len(bus.history) == before + 1
    assert collector.total == 1


def test_collector_correlates_and_measures_logical_latency():
    """Hand-crafted ordered stream -> deterministic logical latency."""
    bus = InMemoryBus()
    collector = TraceCollector(bus, DecisionLogger.null())

    def pub(topic, *, mtype=MessageType.ALERT, payload=None, incident="EQ-1"):
        bus.publish(
            Message(
                sender="x", recipient="y", type=mtype,
                priority=Priority.HIGH, topic=topic,
                module=Module.EARTHQUAKE, incident_id=incident,
                payload=payload or {},
            )
        )

    # seq 1..5 for EQ-1; an unrelated incident's traffic interleaves but must
    # not affect EQ-1's anchors.
    pub(Topic.RAW_FEED)                 # seq 1 (first RAW_FEED anchor)
    pub(Topic.RAW_FEED, incident="EQ-2")  # seq 2 (other incident)
    pub(Topic.PREDICTION)              # seq 3
    pub(Topic.RESOURCE_PLAN)          # seq 4
    pub(Topic.DISPATCH, mtype=MessageType.INSTRUCTION,
        payload={"kind": "dispatch"})  # seq 5 (real DISPATCH anchor)
    # A trailing housekeeping ACK must NOT move the dispatch anchor.
    pub(Topic.DISPATCH, mtype=MessageType.ACK,
        payload={"kind": "dispatch_ack"})  # seq 6 (ignored as anchor)

    # Logical latency = last real DISPATCH (5) - first RAW_FEED (1) = 4.
    assert collector.incident_latency("EQ-1") == 4
    # EQ-2 only had a RAW_FEED, never reached dispatch -> latency None.
    assert collector.incident_latency("EQ-2") is None
    # Unknown incident -> None.
    assert collector.incident_latency("nope") is None

    snap = collector.incident_snapshot("EQ-1")
    assert snap is not None
    assert snap["reached_dispatch"] is True
    assert snap["latency"] == 4
    assert snap["first_raw_feed_seq"] == 1
    assert snap["last_dispatch_seq"] == 5
    # The dispatch_ack receipt is observed (tallied) but not an anchor.
    assert snap["by_topic"][Topic.DISPATCH] == 2
    assert Topic.RAW_FEED in snap["topics"]
    assert "B" in snap["modules"]


def test_collector_tallies_a_driven_earthquake_scenario():
    """Wire the collector onto a scenario's bus, then drive the real pipeline."""
    from disastermind.scenarios.base import build_loop
    from disastermind.scenarios.earthquake import simulate_earthquake, INCIDENT_ID

    loop = build_loop()
    collector = TraceCollector(loop.bus, DecisionLogger.null())
    simulate_earthquake(loop=loop)

    # The collector observed the whole stream and correlated the incident.
    assert collector.total > 0
    assert INCIDENT_ID in collector.incidents
    assert collector.by_topic[Topic.RAW_FEED] >= 1

    snap = collector.incident_snapshot(INCIDENT_ID)
    assert snap is not None
    # The earthquake chain reaches a real DISPATCH, so latency is computable
    # (the collector observed both a RAW_FEED and a real dispatch anchor).
    assert snap["reached_dispatch"] is True
    latency = collector.incident_latency(INCIDENT_ID)
    assert latency is not None
    assert latency == snap["latency"] == (
        snap["last_dispatch_seq"] - snap["first_raw_feed_seq"]
    )
    # Span/logical-step count matches the number of correlated observations.
    assert snap["span_count"] == sum(
        1 for m in loop.bus.history if m.incident_id == INCIDENT_ID
    )
    # Topics traversed include the chain's start and end anchors.
    assert Topic.RAW_FEED in snap["topics"]
    assert Topic.DISPATCH in snap["topics"]

    # The whole-system snapshot is a JSON-serialisable per-incident view.
    whole = collector.snapshot()
    json.dumps(whole)
    assert whole["incident_count"] == len(collector.incidents)
    assert INCIDENT_ID in whole["incidents"]

    # Logical latency is deterministic: re-driving the same scenario on a fresh
    # collector yields the identical value (ordering, never wall clock).
    loop2 = build_loop()
    collector2 = TraceCollector(loop2.bus, DecisionLogger.null())
    simulate_earthquake(loop=loop2)
    assert collector2.incident_latency(INCIDENT_ID) == latency


def test_collector_handles_uncorrelated_messages():
    bus = InMemoryBus()
    collector = TraceCollector(bus, DecisionLogger.null())
    bus.publish(
        Message(
            sender="x", recipient="y", type=MessageType.ALERT,
            priority=Priority.LOW, topic=Topic.RAW_FEED,
            module=Module.ALL, incident_id=None,
        )
    )
    assert collector.total == 1
    assert collector.uncorrelated == 1
    assert collector.incidents == {}


# --------------------------------------------------------------------------- #
# OpenTelemetry export (stub path always; real SDK importorskip)              #
# --------------------------------------------------------------------------- #
class _StubOtelSpan:
    def __init__(self, name):
        self.name = name
        self.attributes = {}
        self.ended = False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def end(self):
        self.ended = True


class _StubOtelTracer:
    def __init__(self):
        self.started = []

    def start_span(self, name):
        s = _StubOtelSpan(name)
        self.started.append(s)
        return s


def test_otel_export_mirrors_closed_spans_to_injected_tracer():
    """The lazy OTel path is exercised with a stub tracer — no SDK needed."""
    rec = SpanRecorder()
    tracer = _StubOtelTracer()
    assert rec.enable_otel(tracer) is True
    assert rec.otel_enabled is True

    clk = _counter_clock()
    with trace("predict", recorder=rec, incident_id="EQ-7", clock=clk) as span:
        span.set("model", "xgboost")

    # The closed span was mirrored to the stub tracer exactly once.
    assert len(tracer.started) == 1
    mirrored = tracer.started[0]
    assert mirrored.name == "predict"
    assert mirrored.ended is True
    assert mirrored.attributes["model"] == "xgboost"
    assert mirrored.attributes["incident_id"] == "EQ-7"
    # In-memory recorder remains the source of truth regardless.
    assert rec.spans == [span]


def test_otel_real_sdk_path_importorskip():
    """Real-SDK enablement is guarded so the default suite needs no optional dep."""
    pytest.importorskip("opentelemetry")
    rec = SpanRecorder()
    # With the SDK importable, enable_otel resolves the global tracer.
    assert rec.enable_otel() is True
    assert rec.otel_enabled is True
    with trace("real-otel", recorder=rec, incident_id="EQ-8"):
        pass
    # In-memory recording still works alongside the real exporter.
    assert any(s.name == "real-otel" for s in rec.spans)


def test_otel_disabled_by_default_is_in_memory_only():
    rec = SpanRecorder()
    assert rec.otel_enabled is False  # opt-in only
    with trace("noop", recorder=rec):
        pass
    assert len(rec.spans) == 1
