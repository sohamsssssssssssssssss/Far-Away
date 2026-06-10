"""Tests for the Commander Dashboard backend (PRD Step 7 + Step 10).

The framework-free :class:`~disastermind.api.service.DashboardService` is tested
against a REAL wired system from :func:`disastermind.orchestration.build_system`
— driving an escalation through the Commander and approving it — with the
standard library only (no FastAPI). The thin FastAPI transport is exercised
separately and guarded by ``pytest.importorskip("fastapi")`` so the suite passes
whether or not FastAPI is installed.
"""
from __future__ import annotations

import pytest

from disastermind.api.service import WS_STREAM, DashboardService
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
from disastermind.orchestration.build import build_system


# ----------------------------------------------------------------- fixtures/helpers
def _build_real_system():
    """A full DisasterMind system on an in-memory bus + its DashboardService."""
    bus = InMemoryBus()
    logger = DecisionLogger.null()
    settings = Settings()
    loop = build_system(bus=bus, logger=logger, settings=settings)
    assert loop.commander is not None, "system has no commander to drive"
    service = DashboardService(bus=bus, commander=loop.commander)
    return bus, loop, service


def _cross_state_field_order(incident_id: str = "usgs:eq-escalate") -> Message:
    """A FIELD_ORDER carrying a cross-state escalation trigger (human review)."""
    return Message(
        sender="field_coordinator",
        recipient="commander",
        type=MessageType.INSTRUCTION,
        priority=Priority.CRITICAL,
        topic=Topic.FIELD_ORDER,
        incident_id=incident_id,
        module=Module.EARTHQUAKE,
        escalation_trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
        payload={
            "kind": "field_order",
            "incident_id": incident_id,
            "orders": [
                {
                    "team_id": "NDRF-99",
                    "site": "cross-border-zone",
                    "priority": 1,
                    "reason": "cross-state mutual aid required",
                }
            ],
            "escalation": {
                "trigger": EscalationTrigger.CROSS_STATE_RESOURCE.value,
                "summary": "needs a neighbouring state's NDRF battalion",
                "scale": 1,
            },
        },
    )


# ----------------------------------------------------------------- service: reads
def test_health_and_topic_counts_track_the_bus():
    bus, loop, service = _build_real_system()
    health = service.health()
    assert health["status"] == "ok"
    assert health["commander"] == "commander"
    assert health["pending_escalations"] == 0

    before = service.topic_counts()
    bus.publish(_cross_state_field_order())
    after = service.topic_counts()
    # The injected FIELD_ORDER (and the resulting ESCALATION) must show up.
    assert after.get(Topic.FIELD_ORDER, 0) == before.get(Topic.FIELD_ORDER, 0) + 1
    assert after.get(Topic.ESCALATION, 0) >= 1


def test_recent_returns_jsonable_messages_newest_last():
    bus, loop, service = _build_real_system()
    bus.publish(_cross_state_field_order("inc-A"))
    recent = service.recent(limit=5)
    assert recent, "recent() returned nothing despite published messages"
    # Each row is a plain JSON-able dict (Message.to_dict shape).
    for row in recent:
        assert isinstance(row, dict)
        assert "topic" in row and "type" in row and "id" in row
    # Newest last: the final row is the most recent message on the bus. (The
    # Group B LLM narrator reacts to the ESCALATION with a follow-up brief, so
    # the tail topic may be that narrative rather than the ESCALATION itself.)
    assert recent[-1]["id"] == bus.history[-1].id
    # ...and the escalation we triggered must surface within the recent window.
    assert any(r["topic"] == Topic.ESCALATION for r in recent)


def test_recent_non_positive_limit_is_empty():
    _, _, service = _build_real_system()
    assert service.recent(limit=0) == []
    assert service.recent(limit=-3) == []


def test_incidents_rollup_groups_by_incident_id():
    bus, loop, service = _build_real_system()
    bus.publish(_cross_state_field_order("inc-X"))
    rows = service.incidents()
    ids = {r["incident_id"] for r in rows}
    assert "inc-X" in ids
    incident = next(r for r in rows if r["incident_id"] == "inc-X")
    assert incident["message_count"] >= 1
    assert Topic.FIELD_ORDER in incident["topics"]
    assert incident["module"] == Module.EARTHQUAKE.value


# --------------------------------------------------- service: escalation lifecycle
def test_escalation_then_approve_drives_a_dispatch():
    """Drive an escalation through the real commander, then approve it."""
    bus, loop, service = _build_real_system()

    # No escalations before we inject the order.
    assert service.list_escalations() == []

    bus.publish(_cross_state_field_order())

    pending = service.list_escalations()
    assert len(pending) == 1, "cross-state order did not register a pending escalation"
    report = pending[0]
    assert report["trigger"] == EscalationTrigger.CROSS_STATE_RESOURCE.value
    assert report["status"] == "pending"
    report_id = report["report_id"]

    # get_escalation finds it.
    assert service.get_escalation(report_id)["report_id"] == report_id

    dispatch_before = sum(
        1
        for m in bus.history
        if m.topic == Topic.DISPATCH and m.type is MessageType.INSTRUCTION
    )

    result = service.approve(report_id, approver="commander_jane")
    assert result["ok"] is True
    assert result["action"] == "approve"
    assert result["dispatched"], "approve produced no dispatch message"

    # The commander really published a DISPATCH on the bus.
    dispatch_after = sum(
        1
        for m in bus.history
        if m.topic == Topic.DISPATCH and m.type is MessageType.INSTRUCTION
    )
    assert dispatch_after == dispatch_before + 1

    # Escalation is no longer pending after approval.
    assert service.get_escalation(report_id) is None
    assert service.list_escalations() == []


def test_reject_clears_pending_without_dispatch():
    bus, loop, service = _build_real_system()
    bus.publish(_cross_state_field_order("inc-reject"))
    report_id = service.list_escalations()[0]["report_id"]

    dispatch_before = sum(
        1
        for m in bus.history
        if m.topic == Topic.DISPATCH and m.type is MessageType.INSTRUCTION
    )
    result = service.reject(report_id, approver="commander_jane", note="not warranted")
    assert result["ok"] is True
    assert result["action"] == "reject"

    # No new INSTRUCTION dispatch; a rejection ACK should exist on ESCALATION.
    dispatch_after = sum(
        1
        for m in bus.history
        if m.topic == Topic.DISPATCH and m.type is MessageType.INSTRUCTION
    )
    assert dispatch_after == dispatch_before
    assert any(
        (m.payload or {}).get("kind") == "escalation_rejected" for m in bus.history
    )
    assert service.list_escalations() == []


def test_approve_unknown_report_is_not_ok():
    _, _, service = _build_real_system()
    result = service.approve("esc-doesnotexist")
    assert result["ok"] is False
    assert result["dispatched"] == []


# ------------------------------------------------------------- service: streaming
def test_streaming_pushes_new_bus_messages_to_listeners():
    bus, loop, service = _build_real_system()
    service.start_streaming()

    received: list[dict] = []
    unsubscribe = service.add_listener(received.append)

    bus.publish(_cross_state_field_order("inc-stream"))

    topics = {r["topic"] for r in received}
    assert Topic.FIELD_ORDER in topics, "listener never saw the FIELD_ORDER"
    # The escalation emitted in response should also stream through.
    assert Topic.ESCALATION in topics

    # After unsubscribe, no further callbacks.
    unsubscribe()
    before = len(received)
    bus.publish(_cross_state_field_order("inc-stream-2"))
    assert len(received) == before


def test_start_streaming_is_idempotent():
    bus, loop, service = _build_real_system()
    service.start_streaming()
    service.start_streaming()  # second call must be a no-op (no double fan-out)

    received: list[dict] = []
    service.add_listener(received.append)
    bus.publish(_cross_state_field_order("inc-once"))
    field_orders = [r for r in received if r["topic"] == Topic.FIELD_ORDER]
    assert len(field_orders) == 1, "message duplicated -> start_streaming not idempotent"


def test_ws_stream_constant_is_a_local_topic_string():
    # We own this constant in our package (never edit core/contracts.py).
    assert isinstance(WS_STREAM, str)
    assert WS_STREAM.startswith("api.")


# --------------------------------------------------- FastAPI transport (optional)
def test_fastapi_app_endpoints_and_escalation_flow():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from disastermind.api.app import create_app

    bus, loop, service = _build_real_system()
    service.start_streaming()
    app = create_app(service)
    client = TestClient(app)

    # health / topics / incidents
    assert client.get("/health").json()["status"] == "ok"
    assert isinstance(client.get("/topics").json(), dict)
    assert isinstance(client.get("/incidents").json(), list)

    # No escalations yet.
    assert client.get("/escalations").json() == []

    # Drive an escalation, then approve it over HTTP.
    bus.publish(_cross_state_field_order("inc-http"))
    escalations = client.get("/escalations").json()
    assert len(escalations) == 1
    report_id = escalations[0]["report_id"]

    resp = client.post(f"/escalations/{report_id}/approve", params={"approver": "http_user"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert client.get("/escalations").json() == []

    # Approving an unknown escalation is a 404.
    assert client.post("/escalations/esc-nope/approve").status_code == 404


def test_fastapi_websocket_streams_new_messages():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from disastermind.api.app import create_app

    bus, loop, service = _build_real_system()
    service.start_streaming()
    app = create_app(service)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "snapshot"
        bus.publish(_cross_state_field_order("inc-ws"))
        # Drain until we observe our FIELD_ORDER (escalation may arrive first).
        seen_topics = set()
        for _ in range(10):
            msg = ws.receive_json()
            seen_topics.add(msg.get("topic"))
            if Topic.FIELD_ORDER in seen_topics:
                break
        assert Topic.FIELD_ORDER in seen_topics


def test_create_app_raises_without_fastapi(monkeypatch):
    """If FastAPI is missing, create_app fails loudly but the package imports."""
    import builtins

    real_import = builtins.__import__

    def _no_fastapi(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ImportError("simulated missing fastapi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_fastapi)
    from disastermind.api.app import create_app

    _, _, service = _build_real_system()
    with pytest.raises(RuntimeError):
        create_app(service)
