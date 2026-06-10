"""Tests for deepened observability metrics + OTLP export (PRD Step 9/10).

This file covers the *additive* observability/tracing deepening:

  * **Prometheus histograms + error counters** — the
    :class:`~disastermind.observability.collector.MetricsCollector` now records a
    per-topic message-processing *latency histogram* (derived from an injected,
    wall-clock-free clock — message ordering, never real time) and *error /
    failure counters*. :func:`~disastermind.observability.exposition.render` emits
    both in valid Prometheus text exposition (``# HELP``/``# TYPE``/``_bucket``/
    ``_sum``/``_count``) while keeping every pre-existing line intact.

  * **OTLP export** — :class:`~disastermind.tracing.spans.SpanRecorder` gains an
    opt-in OTLP export hook (``to_otlp`` serialiser + ``enable_otlp_export`` /
    ``flush_otlp``). It is a *no-op* unless ``DM_OTLP_ENDPOINT`` is set or an
    exporter is injected, so the default path is in-memory only and contacts no
    network. The real-SDK path is guarded with ``pytest.importorskip``.

Stdlib-only and network-free (PRD HARD RULE 2). A tiny in-file Prometheus parser
asserts the exposition is well-formed without any client library.
"""
from __future__ import annotations

import json
import math

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.observability import MetricsCollector, render
from disastermind.observability.collector import (
    DEFAULT_LATENCY_BUCKETS,
    _Histogram,
)
from disastermind.tracing import OTLP_ENDPOINT_ENV, SpanRecorder, trace


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _counter_clock():
    """A monotone integer clock: 1, 2, 3, ... as floats (no wall clock)."""
    state = {"n": 0}

    def tick() -> float:
        state["n"] += 1
        return float(state["n"])

    return tick


def _wire(clock=None) -> tuple[InMemoryBus, MetricsCollector]:
    bus = InMemoryBus()
    collector = MetricsCollector(bus, DecisionLogger.null(), clock=clock)
    return bus, collector


def _msg(topic, *, mtype=MessageType.ALERT, priority=Priority.HIGH, payload=None,
         trigger=None, incident="EQ-1") -> Message:
    return Message(
        sender="x", recipient="y", type=mtype, priority=priority, topic=topic,
        module=Module.EARTHQUAKE, incident_id=incident, payload=payload or {},
        escalation_trigger=trigger,
    )


class _PromParser:
    """A minimal Prometheus text-exposition parser/validator (stdlib only).

    Validates the structural invariants we care about: every non-comment line is
    ``metric{labels} value`` with a numeric value; ``# TYPE`` declarations are
    recognised; histogram families expose ``_bucket``/``_sum``/``_count``; and
    bucket ``le`` values are non-decreasing with a final ``+Inf`` whose count
    equals ``_count`` (the cumulative-histogram invariant).
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.types: dict[str, str] = {}
        self.helps: dict[str, str] = {}
        self.samples: list[tuple[str, dict[str, str], float]] = []
        self._parse()

    @staticmethod
    def _parse_labels(blob: str) -> dict[str, str]:
        labels: dict[str, str] = {}
        if not blob:
            return labels
        # naive but sufficient for our (no-comma-in-value) label set
        for pair in blob.split(","):
            if not pair:
                continue
            k, _, v = pair.partition("=")
            labels[k.strip()] = v.strip().strip('"')
        return labels

    def _parse(self) -> None:
        for line in self.text.splitlines():
            if not line.strip():
                continue
            if line.startswith("# HELP "):
                _, _, rest = line.partition("# HELP ")
                name, _, htext = rest.partition(" ")
                self.helps[name] = htext
                continue
            if line.startswith("# TYPE "):
                _, _, rest = line.partition("# TYPE ")
                name, _, mtype = rest.partition(" ")
                self.types[name] = mtype.strip()
                continue
            assert not line.startswith("#"), f"unexpected comment line: {line!r}"
            # "name{labels} value"  OR  "name value"
            metric, _, value_str = line.rpartition(" ")
            assert metric, f"no metric name in line: {line!r}"
            # value must parse as a float (incl. +Inf / scientific)
            value = float(value_str)
            if "{" in metric:
                name, _, label_blob = metric.partition("{")
                label_blob = label_blob.rstrip("}")
                labels = self._parse_labels(label_blob)
            else:
                name, labels = metric, {}
            self.samples.append((name, labels, value))

    # -- queries -----------------------------------------------------------
    def family(self, name: str) -> str | None:
        """Family name a sample belongs to (strip histogram suffixes)."""
        return self.types.get(name)

    def samples_for(self, name: str) -> list[tuple[dict[str, str], float]]:
        return [(lbls, v) for (n, lbls, v) in self.samples if n == name]

    def assert_histogram_well_formed(self, base: str) -> None:
        assert self.types.get(base) == "histogram", (
            f"{base} not declared as histogram (types={self.types.get(base)})"
        )
        buckets = self.samples_for(f"{base}_bucket")
        sums = self.samples_for(f"{base}_sum")
        counts = self.samples_for(f"{base}_count")
        assert buckets, f"{base} has no _bucket samples"
        assert sums, f"{base} has no _sum sample"
        assert counts, f"{base} has no _count sample"
        # Group buckets per label-set (excluding le) and check cumulative shape.
        per_series: dict[tuple, list[tuple[float, float]]] = {}
        for lbls, v in buckets:
            le = lbls["le"]
            le_val = math.inf if le == "+Inf" else float(le)
            key = tuple(sorted((k, val) for k, val in lbls.items() if k != "le"))
            per_series.setdefault(key, []).append((le_val, v))
        count_by_series = {
            tuple(sorted(lbls.items())): v for lbls, v in counts
        }
        for key, pairs in per_series.items():
            pairs.sort(key=lambda kv: kv[0])
            les = [le for le, _ in pairs]
            vals = [v for _, v in pairs]
            assert les[-1] == math.inf, f"{base} series {key} missing +Inf bucket"
            assert les == sorted(les), f"{base} buckets not ordered: {les}"
            assert vals == sorted(vals), f"{base} cumulative counts not monotone"
            # +Inf cumulative count must equal the _count for that series.
            assert vals[-1] == count_by_series.get(key), (
                f"{base} +Inf bucket != _count for {key}"
            )


# --------------------------------------------------------------------------- #
# _Histogram unit behaviour                                                    #
# --------------------------------------------------------------------------- #
def test_histogram_bucketing_and_cumulative_invariant():
    h = _Histogram((1.0, 2.0, 5.0))
    for v in (0.5, 1.0, 1.5, 3.0, 99.0):
        h.observe(v)
    assert h.count == 5
    assert h.sum == pytest.approx(0.5 + 1.0 + 1.5 + 3.0 + 99.0)
    cum = h.cumulative()
    # (le, cumulative_count): le=1 -> {0.5,1.0}=2 ; le=2 -> +1.5 =3 ; le=5 -> +3.0 =4 ; +Inf -> +99 =5
    assert cum[0] == (1.0, 2)
    assert cum[1] == (2.0, 3)
    assert cum[2] == (5.0, 4)
    assert cum[-1][0] == math.inf and cum[-1][1] == 5
    # The +Inf cumulative count always equals the total observation count.
    assert cum[-1][1] == h.count


# --------------------------------------------------------------------------- #
# Collector: latency histogram derived from an INJECTED clock (no wall clock)  #
# --------------------------------------------------------------------------- #
def test_collector_records_latency_histogram_from_injected_clock():
    # Injected monotone clock => deterministic per-topic inter-arrival latency.
    bus, collector = _wire(clock=_counter_clock())
    # Three RAW_FEED messages: ticks 1,2,3 -> latencies 0 (first),1,1.
    bus.publish(_msg(Topic.RAW_FEED))
    bus.publish(_msg(Topic.RAW_FEED))
    bus.publish(_msg(Topic.RAW_FEED))
    hist = collector.latency_by_topic[Topic.RAW_FEED]
    assert hist.count == 3
    # first obs latency 0, then two deltas of 1 tick each => sum == 2.0
    assert hist.sum == pytest.approx(2.0)
    # Snapshot is JSON serialisable and carries the histogram.
    snap = collector.snapshot()
    json.dumps(snap)
    assert snap["latency_by_topic"][Topic.RAW_FEED]["count"] == 3


def test_collector_uses_explicit_latency_hint_when_present():
    bus, collector = _wire(clock=_counter_clock())
    bus.publish(_msg(Topic.PREDICTION, payload={"latency": 4.0}))
    bus.publish(_msg(Topic.PREDICTION, payload={"processing_seconds": 0.25}))
    hist = collector.latency_by_topic[Topic.PREDICTION]
    assert hist.count == 2
    assert hist.sum == pytest.approx(4.25)


def test_collector_error_counters():
    bus, collector = _wire(clock=_counter_clock())
    bus.publish(_msg(Topic.RAW_FEED, payload={"status": "error"}))
    bus.publish(_msg(Topic.RAW_FEED, payload={"failed": True}))
    bus.publish(_msg(Topic.RAW_FEED, payload={"degraded": True}))
    bus.publish(_msg(Topic.RAW_FEED, payload={"ok": True}))  # healthy -> no count
    assert collector.errors == 3
    assert collector.by_error_kind["error"] == 2  # status=error + failed flag
    assert collector.by_error_kind["degraded"] == 1


def test_default_buckets_are_sorted_and_positive():
    assert list(DEFAULT_LATENCY_BUCKETS) == sorted(DEFAULT_LATENCY_BUCKETS)
    assert all(b > 0 for b in DEFAULT_LATENCY_BUCKETS)


# --------------------------------------------------------------------------- #
# Exposition: render() now contains histogram + counters and still parses      #
# --------------------------------------------------------------------------- #
def _drive_rich_scenario():
    bus, collector = _wire(clock=_counter_clock())
    # A representative spread including an escalation, a real dispatch, an ack,
    # and an error so every new family is exercised.
    bus.publish(_msg(Topic.RAW_FEED))
    bus.publish(_msg(Topic.PREDICTION))
    bus.publish(_msg(Topic.RAW_FEED))  # second RAW_FEED -> a non-zero latency obs
    bus.publish(_msg(Topic.RESOURCE_PLAN))
    bus.publish(_msg(
        Topic.DISPATCH, mtype=MessageType.INSTRUCTION,
        priority=Priority.CRITICAL, payload={"kind": "dispatch", "channel": "sms"},
    ))
    bus.publish(_msg(
        Topic.DISPATCH, mtype=MessageType.ACK, priority=Priority.LOW,
        payload={"kind": "dispatch_ack", "delivered": 1},
    ))
    bus.publish(_msg(
        Topic.ESCALATION, mtype=MessageType.ESCALATION, priority=Priority.CRITICAL,
        trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
    ))
    bus.publish(_msg(Topic.RAW_FEED, payload={"status": "error"}))
    return bus, collector


def test_render_contains_histogram_counter_lines_and_still_parses():
    bus, collector = _drive_rich_scenario()
    text = render(collector)

    # --- pre-existing lines are still intact (back-compat / additive) --------
    assert "# HELP disastermind_messages_total" in text
    assert "# TYPE disastermind_messages_total counter" in text
    assert "disastermind_dispatches_total 1" in text
    assert "disastermind_escalations_total 1" in text
    assert (
        f'disastermind_escalations_by_trigger_total{{trigger="'
        f'{EscalationTrigger.CROSS_STATE_RESOURCE.value}"}} 1'
    ) in text
    assert "# TYPE disastermind_collector_uptime_seconds gauge" in text

    # --- NEW: error counters --------------------------------------------------
    assert "# TYPE disastermind_errors_total counter" in text
    assert "disastermind_errors_total 1" in text
    assert 'disastermind_errors_by_kind_total{kind="error"} 1' in text

    # --- NEW: per-topic latency histogram ------------------------------------
    base = "disastermind_message_processing_latency_seconds"
    assert f"# TYPE {base} histogram" in text
    assert f"{base}_bucket{{" in text
    assert f"{base}_sum{{" in text
    assert f"{base}_count{{" in text
    # A +Inf bucket must be present.
    assert 'le="+Inf"' in text

    # --- the whole document parses as valid Prometheus text ------------------
    parser = _PromParser(text)
    # Every counter/gauge family declared has at least one sample.
    assert parser.types["disastermind_messages_total"] == "counter"
    assert parser.types[base] == "histogram"
    parser.assert_histogram_well_formed(base)

    # The text is newline-terminated (as before).
    assert text.endswith("\n")


def test_render_histogram_count_matches_observations():
    bus, collector = _wire(clock=_counter_clock())
    for _ in range(4):
        bus.publish(_msg(Topic.RAW_FEED))
    text = render(collector)
    parser = _PromParser(text)
    base = "disastermind_message_processing_latency_seconds"
    counts = parser.samples_for(f"{base}_count")
    raw_feed_count = [
        v for lbls, v in counts if lbls.get("topic") == Topic.RAW_FEED
    ]
    assert raw_feed_count == [4.0]
    parser.assert_histogram_well_formed(base)


def test_render_with_no_traffic_still_valid_and_has_no_histogram_series():
    bus, collector = _wire(clock=_counter_clock())
    text = render(collector)
    parser = _PromParser(text)  # must not raise
    base = "disastermind_message_processing_latency_seconds"
    # Family header present (additive) but no per-topic series yet.
    assert parser.types.get(base) == "histogram"
    assert parser.samples_for(f"{base}_bucket") == []
    assert "disastermind_errors_total 0" in text


# --------------------------------------------------------------------------- #
# OTLP export: opt-in, no-op without DM_OTLP_ENDPOINT, pure serialiser         #
# --------------------------------------------------------------------------- #
def test_to_otlp_is_pure_serialiser_and_json_round_trips():
    rec = SpanRecorder()
    clk = _counter_clock()
    with trace("predict", recorder=rec, incident_id="EQ-7", clock=clk) as span:
        span.set("model", "xgboost").set("count", 3).set("ok", True)

    envelope = rec.to_otlp()
    json.dumps(envelope)  # must serialise
    rs = envelope["resourceSpans"][0]
    otlp_spans = rs["scopeSpans"][0]["spans"]
    assert len(otlp_spans) == 1
    sp = otlp_spans[0]
    assert sp["name"] == "predict"
    assert sp["spanId"] == span.span_id
    assert sp["traceId"] == "EQ-7"  # incident becomes the trace id
    # incident_id is surfaced as an attribute; typed AnyValue wrappers are used.
    attrs = {a["key"]: a["value"] for a in sp["attributes"]}
    assert attrs["model"] == {"stringValue": "xgboost"}
    assert attrs["count"] == {"intValue": 3}
    assert attrs["ok"] == {"boolValue": True}
    assert attrs["incident_id"] == {"stringValue": "EQ-7"}
    # status OK maps to code 1; timestamps are unsigned nanoseconds.
    assert sp["status"]["code"] == 1
    assert sp["endTimeUnixNano"] >= sp["startTimeUnixNano"] >= 0


def test_otlp_export_is_noop_without_endpoint_or_exporter(monkeypatch):
    monkeypatch.delenv(OTLP_ENDPOINT_ENV, raising=False)
    rec = SpanRecorder()
    assert rec.otlp_enabled is False
    # enable_otlp_export with nothing configured stays off (opt-in only).
    assert rec.enable_otlp_export() is False
    assert rec.otlp_enabled is False
    with trace("noop", recorder=rec):
        pass
    # Nothing to flush; flush is a harmless 0 and never contacts a network.
    assert rec.flush_otlp() == 0
    # In-memory recording is unaffected.
    assert len(rec.spans) == 1


def test_otlp_export_enabled_via_env_with_injected_exporter(monkeypatch):
    """DM_OTLP_ENDPOINT opts in; an injected exporter proves the no-network path."""
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, "http://collector.local:4318/v1/traces")
    exported: list = []

    def stub_exporter(spans):
        exported.extend(spans)

    rec = SpanRecorder()
    # endpoint resolved from env, but we inject the exporter so no socket opens.
    assert rec.enable_otlp_export(exporter=stub_exporter) is True
    assert rec.otlp_enabled is True

    clk = _counter_clock()
    with trace("dispatch", recorder=rec, incident_id="EQ-9", clock=clk):
        pass
    # The closed span was pushed to the exporter exactly once on end.
    assert len(exported) == 1 and exported[0].name == "dispatch"
    # Flushing re-exports all recorded spans (batch path).
    assert rec.flush_otlp() == 1


def test_enable_otlp_export_reads_explicit_endpoint_arg(monkeypatch):
    monkeypatch.delenv(OTLP_ENDPOINT_ENV, raising=False)
    sent: list = []
    rec = SpanRecorder()
    assert rec.enable_otlp_export(
        "http://x:4318", exporter=lambda spans: sent.extend(spans)
    ) is True
    with trace("a", recorder=rec):
        pass
    assert len(sent) == 1


def test_enable_otlp_export_reads_endpoint_from_injected_env_mapping():
    rec = SpanRecorder()
    # No real OTel exporter is wired here (env points nowhere reachable) but the
    # opt-in resolves the endpoint without touching os.environ or a socket.
    out = rec.enable_otlp_export(env={OTLP_ENDPOINT_ENV: "http://c:4318"})
    # Either the lazy SDK exporter was built (out True) or unavailable (False),
    # but in both cases the endpoint is recorded and no network was contacted.
    assert rec._otlp_endpoint == "http://c:4318"
    assert isinstance(out, bool)


def test_otlp_real_sdk_path_importorskip():
    """Real OTLP SDK exporter build is guarded so the suite needs no optional dep."""
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    rec = SpanRecorder()
    # With the exporter SDK importable, enabling via endpoint builds a real
    # exporter object (constructed only; no export/network is performed here).
    enabled = rec.enable_otlp_export("http://localhost:4318/v1/traces")
    assert enabled is True
    assert rec.otlp_enabled is True
    with trace("real-otlp", recorder=rec, incident_id="EQ-OTLP"):
        pass
    # In-memory recording still works alongside the real exporter wiring.
    assert any(s.name == "real-otlp" for s in rec.spans)
    # The pure serialiser remains available regardless of the SDK path.
    env = rec.to_otlp()
    assert env["resourceSpans"][0]["scopeSpans"][0]["spans"]
