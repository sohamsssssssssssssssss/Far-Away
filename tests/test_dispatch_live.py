"""Tier-3 dispatch: live-send paths, CAP emergency broadcast, resilience.

These tests exercise the *deepened* dispatch layer (PRD Step 8) while staying
fully deterministic and **network-free** — every "live" path is driven through
an INJECTED transport stub (no real socket is ever opened) and the circuit
breaker uses an injected clock / explicit thresholds.

Covered:
  * dry-run (default) records the would-be send and returns a stable receipt;
  * a ``public_alert`` DISPATCH renders a well-formed CAP 1.2 XML document
    (via :func:`disastermind.alerting.build_cap_alert`) that re-parses cleanly;
  * a live send with an injected transport stub calls it exactly once;
  * the circuit breaker OPENS after repeated transport failures and then
    fast-fails subsequent sends without touching the transport;
  * the DispatchRouter routes a public_alert order to the CAP channel and
    surfaces the rendered CAP XML on its ACK;
  * graceful degradation when credentials / SDKs are absent (still no network).
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from disastermind.alerting import CAP_NAMESPACE
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import Message, MessageType, Priority, Topic
from disastermind.llm import DecisionSupportAdvisor, PublicAlert, TemplateClient
from disastermind.models.domain import DisasterEvent, EventKind
from disastermind.models.geo import LatLon
from disastermind.ops import BreakerState, CircuitBreaker
from disastermind.tier3.dispatch import (
    CapChannel,
    DispatchRouter,
    FcmPushChannel,
    FieldRadioChannel,
    IridiumChannel,
    SmsChannel,
    build_agents,
    build_channels,
)
from disastermind.tier3.dispatch.channels import _live_from_env, _receipt


# --------------------------------------------------------------------------- helpers
def _event(kind: EventKind = EventKind.CYCLONE) -> DisasterEvent:
    return DisasterEvent(
        incident_id="inc-cap-1",
        kind=kind,
        epicentre=LatLon(19.80, 85.82),
        severity=4.0,
        detected_at="2026-06-09T10:00:00Z",
        source="IMD",
        meta={"place": "Puri"},
    )


def _public_alerts(event: DisasterEvent) -> list[PublicAlert]:
    return DecisionSupportAdvisor(client=TemplateClient()).draft_public_alert(event)


class _StubTransport:
    """A recording, network-free transport. ``mode`` selects its behaviour."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.calls: list[dict] = []

    def __call__(self, wire: dict) -> dict:
        self.calls.append(wire)
        if self.mode == "raise":
            raise RuntimeError("simulated gateway failure")
        return _receipt(
            "stub", "sent", ["+910000000000"], provider="stub",
            dry_run=False, detail="stub delivered",
        )


def _dispatch_msg(payload: dict) -> Message:
    return Message(
        sender="tier1.commander",
        recipient="dispatch.router",
        type=MessageType.INSTRUCTION,
        priority=Priority.CRITICAL,
        payload=payload,
        topic=Topic.DISPATCH,
        incident_id="inc-cap-1",
    )


# ============================================================ dry-run (default off)
def test_dry_run_records_send_and_returns_receipt():
    """Default channels are dry-run: they record, never touch a network."""
    ch = SmsChannel()  # dry_run defaults True
    assert ch.dry_run is True
    assert ch.is_live() is False

    receipt = ch.send({"recipients": ["+919812345678"], "body": "Evacuate now."})

    assert receipt["kind"] == "dispatch_receipt"
    assert receipt["status"] == "recorded"
    assert receipt["dry_run"] is True
    assert receipt["recipients"] == ["+919812345678"]
    # the rendered wire form is attached for inspection / audit
    assert receipt["wire"]["transport"] == "sms"
    assert receipt["wire"]["text"] == "Evacuate now."
    # and it is appended to the channel outbox
    assert ch.outbox == [receipt]


def test_every_channel_dry_run_records_a_receipt():
    s = Settings()
    channels = build_channels(s, dry_run=True)
    assert [c.name for c in channels] == ["sms", "push", "iridium", "cap", "radio"]
    for ch in channels:
        r = ch.send({"recipients": ["dest"], "body": "hello", "title": "t"})
        assert r["status"] == "recorded"
        assert r["dry_run"] is True
        assert "wire" in r


def test_build_is_pure_no_send():
    """build() renders the wire form without sending or recording."""
    ch = FcmPushChannel()
    wire = ch.build({"recipients": ["tok1", "tok2"], "body": "B", "title": "T"})
    assert wire["transport"] == "fcm"
    assert wire["registration_ids"] == ["tok1", "tok2"]
    assert wire["notification"]["title"] == "T"
    assert ch.outbox == []  # building does not record


# ============================================================ CAP emergency broadcast
def test_public_alert_payload_renders_wellformed_cap_xml():
    event = _event(EventKind.CYCLONE)
    alerts = _public_alerts(event)
    ch = CapChannel()
    xml = ch.to_xml(
        {
            "kind": "public_alert",
            "event": event,
            "public_alerts": alerts,
            "area_desc": "Puri coastal belt",
        }
    )
    # re-parses cleanly -> well-formed
    root = ET.fromstring(xml)
    assert root.tag == f"{{{CAP_NAMESPACE}}}alert"
    # one <info> per language (en, hi, or)
    infos = root.findall(f"{{{CAP_NAMESPACE}}}info")
    assert len(infos) == len(alerts) == 3
    # CYCLONE maps to Met category + Severe/Immediate via build_cap_alert
    first = infos[0]
    assert first.findtext(f"{{{CAP_NAMESPACE}}}category") == "Met"
    assert first.findtext(f"{{{CAP_NAMESPACE}}}severity") == "Severe"
    assert first.findtext(f"{{{CAP_NAMESPACE}}}urgency") == "Immediate"
    # area description carried through
    area = first.find(f"{{{CAP_NAMESPACE}}}area")
    assert area.findtext(f"{{{CAP_NAMESPACE}}}areaDesc") == "Puri coastal belt"


def test_public_alert_with_polygon_emits_cap_polygon():
    event = _event(EventKind.FLOOD)
    alerts = _public_alerts(event)
    ring = [LatLon(19.7, 85.7), LatLon(19.9, 85.7), LatLon(19.9, 85.9)]
    ch = CapChannel()
    xml = ch.to_xml(
        {
            "kind": "public_alert",
            "event": event,
            "public_alerts": alerts,
            "area_desc": "flood zone",
            "polygon": ring,
        }
    )
    root = ET.fromstring(xml)
    poly = root.find(
        f"{{{CAP_NAMESPACE}}}info/{{{CAP_NAMESPACE}}}area/{{{CAP_NAMESPACE}}}polygon"
    )
    assert poly is not None and poly.text
    # auto-closed ring: first coord repeats as the last
    coords = poly.text.split()
    assert coords[0] == coords[-1]


def test_cap_falls_back_to_inline_xml_without_event():
    """A bare CAP order (no DisasterEvent) still renders valid inline CAP XML."""
    ch = CapChannel()
    xml = ch.to_xml({"title": "Manual Alert", "body": "Move now", "areas": ["Zone-7"]})
    root = ET.fromstring(xml)
    assert root.tag.endswith("alert")
    assert "Zone-7" in xml
    assert "Manual Alert" in xml


def test_cap_dry_run_send_carries_xml_in_receipt():
    event = _event()
    ch = CapChannel()
    r = ch.send(
        {
            "kind": "public_alert",
            "event": event,
            "public_alerts": _public_alerts(event),
            "area_desc": "coast",
            "recipients": ["broadcast"],
        }
    )
    assert r["status"] == "recorded"
    assert "xml" in r["wire"]
    ET.fromstring(r["wire"]["xml"])  # well-formed


# ============================================================ live send (stub transport)
def test_live_send_calls_injected_transport_exactly_once():
    stub = _StubTransport(mode="ok")
    ch = SmsChannel(dry_run=False, live=True, transport=stub)
    assert ch.is_live() is True

    receipt = ch.send({"recipients": ["+919812345678"], "body": "Go now"})

    assert len(stub.calls) == 1  # transport hit exactly once, no real socket
    assert stub.calls[0]["transport"] == "sms"
    assert receipt["status"] == "sent"
    assert receipt["dry_run"] is False


def test_live_send_each_channel_uses_transport_once():
    for cls in (SmsChannel, FcmPushChannel, IridiumChannel, FieldRadioChannel):
        stub = _StubTransport(mode="ok")
        ch = cls(dry_run=False, live=True, transport=stub)
        ch.send({"recipients": ["d"], "body": "b", "title": "t"})
        assert len(stub.calls) == 1, cls.__name__


def test_live_cap_send_uses_transport_and_attaches_xml():
    event = _event()
    stub = _StubTransport(mode="ok")
    ch = CapChannel(dry_run=False, live=True, transport=stub)
    r = ch.send(
        {
            "kind": "public_alert",
            "event": event,
            "public_alerts": _public_alerts(event),
            "area_desc": "coast",
        }
    )
    assert len(stub.calls) == 1
    assert stub.calls[0]["transport"] == "cap"
    ET.fromstring(stub.calls[0]["xml"])  # transport received well-formed CAP
    assert "xml" in r["wire"]


def test_live_send_without_credentials_degrades_no_network():
    """No transport, no creds, live=True -> recorded receipt (never a socket)."""
    ch = SmsChannel(dry_run=False, live=True, settings=Settings())  # no twilio creds
    r = ch.send({"recipients": ["+910000000000"], "body": "hi"})
    assert r["status"] == "recorded"
    assert "no twilio credentials" in r["detail"]


def test_dry_run_ignores_injected_transport():
    """Even with a transport set, dry-run must not call it."""
    stub = _StubTransport(mode="ok")
    ch = SmsChannel(dry_run=True, transport=stub)
    ch.send({"recipients": ["x"], "body": "b"})
    assert stub.calls == []


# ============================================================ circuit breaker
def test_circuit_breaker_opens_on_repeated_send_failures():
    stub = _StubTransport(mode="raise")
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=30.0)
    ch = SmsChannel(dry_run=False, live=True, transport=stub, breaker=breaker)

    # three consecutive transport failures trip the breaker
    for _ in range(3):
        r = ch.send({"recipients": ["x"], "body": "b"})
        assert r["status"] == "failed"
    assert breaker.state is BreakerState.OPEN
    assert breaker.is_open is True
    assert len(stub.calls) == 3

    # once OPEN, further sends fast-fail WITHOUT hitting the transport again
    r = ch.send({"recipients": ["x"], "body": "b"})
    assert r["status"] == "failed"
    assert "OPEN" in r["detail"]
    assert len(stub.calls) == 3  # transport untouched while breaker is open


def test_circuit_breaker_half_open_recovers_after_cooldown():
    clock = {"t": 1000.0}
    stub = _StubTransport(mode="raise")
    breaker = CircuitBreaker(
        failure_threshold=2, reset_timeout=10.0, clock=lambda: clock["t"]
    )
    ch = SmsChannel(dry_run=False, live=True, transport=stub, breaker=breaker)

    for _ in range(2):
        ch.send({"recipients": ["x"], "body": "b"})
    assert breaker.is_open is True

    # advance past the cooldown and let a healthy transport probe through
    clock["t"] += 11.0
    ch.transport = _StubTransport(mode="ok")
    r = ch.send({"recipients": ["x"], "body": "b"})
    assert r["status"] == "sent"
    assert breaker.is_closed is True


def test_breaker_built_lazily_when_absent():
    """A channel without an injected breaker builds one on first live send."""
    stub = _StubTransport(mode="ok")
    ch = IridiumChannel(dry_run=False, live=True, transport=stub)
    assert ch.breaker is None
    ch.send({"recipients": ["123456789012345"], "body": "b"})
    assert ch.breaker is not None  # lazily created


# ============================================================ retry wrapper
def test_send_attempts_retries_transient_failures():
    calls = {"n": 0}

    def flaky(wire):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return _receipt("stub", "sent", ["x"], "stub", False, "ok on 3rd")

    ch = SmsChannel(dry_run=False, live=True, transport=flaky, send_attempts=3)
    r = ch.send({"recipients": ["x"], "body": "b"})
    assert calls["n"] == 3
    assert r["status"] == "sent"


# ============================================================ router CAP integration
def _router_with_channels() -> tuple[DispatchRouter, InMemoryBus]:
    bus = InMemoryBus()
    s = Settings()
    channels = build_channels(s, dry_run=True)
    router = DispatchRouter(bus=bus, channels=channels, settings=s)
    return router, bus


def test_router_routes_public_alert_to_cap_and_surfaces_xml():
    router, _ = _router_with_channels()
    event = _event()
    msg = _dispatch_msg(
        {
            "kind": "public_alert",
            "channel": "cap",
            "event": event,
            "public_alerts": _public_alerts(event),
            "area_desc": "Puri coastal belt",
        }
    )
    out = router.handle(msg)
    assert len(out) == 1
    ack = out[0]
    assert ack.type is MessageType.ACK
    assert ack.payload["kind"] == "dispatch_ack"
    # CAP channel delivered (recorded in dry-run) and XML hoisted to the ACK
    cap_receipts = [r for r in ack.payload["receipts"] if r["channel"] == "cap"]
    assert len(cap_receipts) == 1
    assert "cap_xml" in ack.payload
    root = ET.fromstring(ack.payload["cap_xml"])
    assert root.tag == f"{{{CAP_NAMESPACE}}}alert"


def test_router_public_alert_defaults_to_cap_even_when_channel_unset():
    router, _ = _router_with_channels()
    event = _event()
    msg = _dispatch_msg(
        {
            "kind": "public_alert",
            "event": event,
            "public_alerts": _public_alerts(event),
            "area_desc": "zone",
        }
    )
    ack = router.handle(msg)[0]
    channels_used = {r["channel"] for r in ack.payload["receipts"]}
    assert "cap" in channels_used
    assert "cap_xml" in ack.payload


def test_router_public_alert_all_channels_includes_cap():
    router, _ = _router_with_channels()
    event = _event()
    msg = _dispatch_msg(
        {
            "kind": "public_alert",
            "channel": "all",
            "event": event,
            "public_alerts": _public_alerts(event),
            "area_desc": "zone",
            "recipients": ["+911111111111"],
        }
    )
    ack = router.handle(msg)[0]
    channels_used = {r["channel"] for r in ack.payload["receipts"]}
    assert "cap" in channels_used
    # an "all" fan-out also reaches sms/push
    assert {"sms", "push"} <= channels_used


def test_router_ignores_its_own_ack():
    router, _ = _router_with_channels()
    ack_msg = _dispatch_msg({"kind": "dispatch_ack"})
    assert router.handle(ack_msg) == []


def test_router_publishes_ack_on_bus_end_to_end():
    bus = InMemoryBus()
    s = Settings()
    agents = build_agents(bus, logger=None, settings=s)  # type: ignore[arg-type]
    router = agents[0]
    for topic in router.subscriptions:
        bus.subscribe(topic, router.name, lambda m: [bus.publish(o) for o in router.handle(m)])

    event = _event()
    bus.publish(
        _dispatch_msg(
            {
                "kind": "public_alert",
                "channel": "cap",
                "event": event,
                "public_alerts": _public_alerts(event),
                "area_desc": "coast",
            }
        )
    )
    acks = [m for m in bus.history if (m.payload or {}).get("kind") == "dispatch_ack"]
    assert acks, "router should have published a dispatch_ack"
    assert "cap_xml" in acks[-1].payload


# ============================================================ live env switch
def test_default_build_agents_is_dry_run_offline(monkeypatch):
    monkeypatch.delenv("DM_DISPATCH_LIVE", raising=False)
    agents = build_agents(InMemoryBus(), logger=None, settings=Settings())  # type: ignore[arg-type]
    router = agents[0]
    for ch in router.channels.values():
        assert ch.dry_run is True
        assert ch.is_live() is False


def test_live_env_switch_arms_channels(monkeypatch):
    monkeypatch.setenv("DM_DISPATCH_LIVE", "1")
    assert _live_from_env() is True
    agents = build_agents(InMemoryBus(), logger=None, settings=Settings())  # type: ignore[arg-type]
    router = agents[0]
    for ch in router.channels.values():
        assert ch.dry_run is False
        assert ch.is_live() is True


def test_live_none_consults_env(monkeypatch):
    """live=None defers to DM_DISPATCH_LIVE when dry_run is False."""
    monkeypatch.setenv("DM_DISPATCH_LIVE", "1")
    ch = SmsChannel(dry_run=False, live=None)
    assert ch.is_live() is True
    monkeypatch.setenv("DM_DISPATCH_LIVE", "0")
    ch2 = SmsChannel(dry_run=False, live=None)
    assert ch2.is_live() is False
