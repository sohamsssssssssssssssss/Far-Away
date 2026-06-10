"""Contract-completeness + hardening tests for the dashboard API.

Covers the additive surface layered onto the existing transport in
:mod:`disastermind.api.app` (every prior route/behaviour must still pass — see
``test_api.py`` / ``test_api_production.py``):

  * HISTORY endpoints — ``GET /history/incidents`` and ``GET /audit/search``
    respond on the in-memory fallback path (no persistor wired) AND on the durable
    path (a wired ``StatePersistor`` whose ``Storage`` mirrors the bus);
  * API VERSIONING — every data route is also mounted under ``/v1/*`` and the
    versioned alias mirrors the unversioned route byte-for-byte;
  * REQUEST HARDENING — an oversize body is rejected with HTTP 413 (JSON
    envelope) and malformed JSON yields the 400 ``invalid_json`` envelope;
  * IDEMPOTENCY — a repeated ``Idempotency-Key`` on ``/approve`` returns the first
    recorded result WITHOUT re-dispatching (no double-approve);
  * WEBSOCKET hardening — a server-side heartbeat ``ping`` arrives on idle, and a
    concurrent-connection cap closes the excess socket.

FastAPI is optional (HARD RULE 2), so everything is guarded by
``pytest.importorskip("fastapi")``; nothing here touches the network.
"""
from __future__ import annotations

import pytest

from disastermind.api.service import DashboardService
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
from disastermind.tier1.commander.agent import CommanderAgent


# ----------------------------------------------------------------- helpers
def _cross_state_field_order(incident_id: str = "inc-complete") -> Message:
    """A FIELD_ORDER carrying a cross-state trigger -> a pending escalation."""
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
            "orders": [{"team_id": "NDRF-9", "site": "border", "priority": 1}],
            "escalation": {
                "trigger": EscalationTrigger.CROSS_STATE_RESOURCE.value,
                "summary": "needs a neighbouring state's NDRF battalion",
                "scale": 1,
            },
        },
    )


def _plain_message(incident_id: str, topic: str, *, payload: dict | None = None) -> Message:
    return Message(
        sender="x",
        recipient="y",
        type=MessageType.ALERT,
        priority=Priority.INFO,
        topic=topic,
        incident_id=incident_id,
        module=Module.EARTHQUAKE,
        payload=payload or {},
    )


def _real_system():
    """A full wired system on an in-memory bus + its DashboardService + loop."""
    from disastermind.orchestration.build import build_system

    bus = InMemoryBus()
    loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    service = DashboardService(bus=bus, commander=loop.commander)
    service.start_streaming()
    return bus, loop, service


def _bare_service():
    """A minimal service with no orchestration loop (in-memory fallback path)."""
    bus = InMemoryBus()
    service = DashboardService(bus=bus, commander=CommanderAgent(bus=bus))
    service.start_streaming()
    return bus, service


def _client(app, **kw):
    from fastapi.testclient import TestClient

    return TestClient(app, **kw)


class _FakeLoop:
    """A stand-in loop exposing only ``agents`` for ``_find_persisted_storage``."""

    def __init__(self, agents):
        self.agents = list(agents)


# ----------------------------------------------------- history: in-memory fallback
def test_history_incidents_in_memory_fallback():
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app

    bus, service = _bare_service()
    bus.publish(_plain_message("inc-h1", Topic.RAW_FEED))
    bus.publish(_plain_message("inc-h1", Topic.DISPATCH))
    bus.publish(_plain_message("inc-h2", Topic.RAW_FEED))
    client = _client(create_app(service))  # no loop -> in-memory roll-up

    rows = client.get("/history/incidents").json()
    assert isinstance(rows, list)  # legacy bare-list shape (no pagination params)
    ids = {r["incident_id"] for r in rows}
    assert {"inc-h1", "inc-h2"} <= ids
    h1 = next(r for r in rows if r["incident_id"] == "inc-h1")
    assert h1["message_count"] == 2


def test_audit_search_in_memory_fallback_matches_and_filters():
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app

    bus, service = _bare_service()
    bus.publish(_plain_message("inc-a", Topic.RAW_FEED, payload={"needle": "FINDME"}))
    bus.publish(_plain_message("inc-b", Topic.RAW_FEED, payload={"other": "value"}))
    client = _client(create_app(service))

    hits = client.get("/audit/search", params={"q": "findme"}).json()
    assert isinstance(hits, list)
    assert len(hits) == 1
    assert hits[0]["incident_id"] == "inc-a"

    # A non-matching query returns an empty list (not an error).
    assert client.get("/audit/search", params={"q": "no-such-token"}).json() == []
    # No query returns the whole (bounded) bus history.
    assert len(client.get("/audit/search").json()) == 2


# --------------------------------------------------------- history: durable store
def test_history_and_audit_use_durable_store_when_persistor_wired():
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app
    from disastermind.persistence.build import build_agents

    bus = InMemoryBus()
    (persistor,) = build_agents(bus, DecisionLogger.null(), Settings())
    assert persistor.name == "persistence.state"

    # Publish through the bus so the persistor mirrors every message into its
    # durable Storage (Elasticsearch audit fallback) — the history routes then
    # read from the store, NOT the live bus.
    for _ in range(3):
        bus.publish(_plain_message("inc-dur", Topic.RAW_FEED))
    bus.publish(_plain_message("inc-other", Topic.DISPATCH, payload={"tag": "UNIQUEWORD"}))
    assert persistor.storage.audit.count() == 4

    service = DashboardService(bus=bus, commander=CommanderAgent(bus=bus))
    app = create_app(service, loop=_FakeLoop([persistor]))
    client = _client(app)

    rows = client.get("/history/incidents").json()
    ids = {r["incident_id"] for r in rows}
    assert {"inc-dur", "inc-other"} <= ids
    durable = next(r for r in rows if r["incident_id"] == "inc-dur")
    assert durable["message_count"] == 3  # rolled up from the durable audit trail

    hits = client.get("/audit/search", params={"q": "uniqueword"}).json()
    assert len(hits) == 1
    assert hits[0]["incident_id"] == "inc-other"


# --------------------------------------------------------------- /v1 versioning
def test_v1_aliases_mirror_unversioned_routes():
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app

    bus, service = _bare_service()
    bus.publish(_plain_message("inc-v1", Topic.RAW_FEED, payload={"k": "mirror"}))
    client = _client(create_app(service))

    for path in (
        "/topics",
        "/incidents",
        "/recent",
        "/escalations",
        "/history/incidents",
    ):
        legacy = client.get(path)
        versioned = client.get("/v1" + path)
        assert legacy.status_code == versioned.status_code == 200
        assert legacy.json() == versioned.json(), f"/v1 alias differs for {path}"

    # Query-param routes mirror too (with identical params).
    assert (
        client.get("/audit/search", params={"q": "mirror"}).json()
        == client.get("/v1/audit/search", params={"q": "mirror"}).json()
    )
    # The pagination envelope is identical on both prefixes.
    a = client.get("/incidents", params={"limit": 1}).json()
    b = client.get("/v1/incidents", params={"limit": 1}).json()
    assert a == b
    assert set(a) == {"items", "total", "limit", "offset"}


def test_v1_routes_are_registered_on_the_app():
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app

    bus, service = _bare_service()
    app = create_app(service)
    paths = {getattr(r, "path", None) for r in app.routes}
    for p in (
        "/v1/topics",
        "/v1/incidents",
        "/v1/recent",
        "/v1/escalations",
        "/v1/history/incidents",
        "/v1/audit/search",
        "/v1/escalations/{report_id}/approve",
        "/v1/escalations/{report_id}/reject",
        "/v1/ws",
    ):
        assert p in paths, f"missing versioned route {p}"


# ----------------------------------------------------- request hardening: 413
def test_oversize_body_is_rejected_with_413(monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("DM_MAX_BODY", "1024")
    from disastermind.api.app import create_app

    bus, service = _bare_service()
    client = _client(create_app(service))

    # A body over the configured ceiling is rejected BEFORE the route runs.
    resp = client.post("/escalations/whatever/approve", content=b"x" * 4096)
    assert resp.status_code == 413
    body = resp.json()
    assert body["error"]["type"] == "payload_too_large"
    assert "1024" in body["error"]["detail"]

    # A tiny body still reaches the route (which 404s on an unknown escalation).
    small = client.post("/escalations/whatever/approve", content=b"x")
    assert small.status_code == 404


def test_oversize_body_413_also_on_v1(monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("DM_MAX_BODY", "512")
    from disastermind.api.app import create_app

    bus, service = _bare_service()
    client = _client(create_app(service))
    resp = client.post("/v1/escalations/whatever/approve", content=b"y" * 2048)
    assert resp.status_code == 413


def test_malformed_json_returns_400_envelope():
    pytest.importorskip("fastapi")
    from fastapi import Body

    from disastermind.api.app import create_app

    bus, service = _bare_service()
    app = create_app(service)

    # NB: this test module uses ``from __future__ import annotations``, so a
    # Pydantic-model parameter annotation would stringify and (for a locally
    # defined model) be unresolvable by FastAPI's ``get_type_hints`` — FastAPI
    # would then treat it as a query param and never parse the body. We force a
    # JSON-body parameter with an explicit ``Body(...)`` default instead.
    def _probe(value: int = Body(..., embed=True)) -> dict:  # pragma: no cover - via client
        return {"value": value}

    app.add_api_route("/_json_probe", _probe, methods=["POST"])

    client = _client(app)
    bad = client.post(
        "/_json_probe",
        content=b"{ this is not json",
        headers={"content-type": "application/json"},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["type"] == "invalid_json"

    # A well-formed JSON with a wrong type is a schema error (422), not invalid_json.
    typed = client.post("/_json_probe", json={"value": "not-an-int"})
    assert typed.status_code == 422
    assert typed.json()["error"]["type"] == "validation_error"


# --------------------------------------------------------------- idempotency
def test_repeated_idempotency_key_does_not_double_approve():
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app

    bus, loop, service = _real_system()
    client = _client(create_app(service, loop=loop))

    bus.publish(_cross_state_field_order("inc-idem"))
    pending = client.get("/escalations").json()
    assert len(pending) == 1
    report_id = pending[0]["report_id"]

    def _dispatch_count() -> int:
        return sum(
            1
            for m in bus.history
            if m.topic == Topic.DISPATCH and m.type is MessageType.INSTRUCTION
        )

    before = _dispatch_count()
    headers = {"Idempotency-Key": "approve-once"}
    first = client.post(
        f"/escalations/{report_id}/approve", params={"approver": "jane"}, headers=headers
    )
    assert first.status_code == 200
    assert first.json()["ok"] is True
    after_first = _dispatch_count()
    assert after_first == before + 1, "first approve should dispatch exactly once"

    # Same key again: the FIRST result is replayed and NOTHING is re-dispatched.
    second = client.post(
        f"/escalations/{report_id}/approve", params={"approver": "jane"}, headers=headers
    )
    assert second.status_code == 200
    payload = second.json()
    assert payload["ok"] is True
    assert payload.get("idempotent_replay") is True
    assert _dispatch_count() == after_first, "repeated key must not re-dispatch"

    # The escalation was cleared by the first approval; without the key a retry now
    # 404s (proving the replay served from cache, not the live commander).
    assert client.post(f"/escalations/{report_id}/approve").status_code == 404


def test_idempotency_key_scoped_per_action_and_report():
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app

    bus, loop, service = _real_system()
    client = _client(create_app(service, loop=loop))

    bus.publish(_cross_state_field_order("inc-scope-a"))
    bus.publish(_cross_state_field_order("inc-scope-b"))
    pend = {p["report_id"] for p in client.get("/escalations").json()}
    assert len(pend) == 2
    rid_a, rid_b = sorted(pend)

    # Same header value but a different report id must act independently (no alias).
    h = {"Idempotency-Key": "shared"}
    ra = client.post(f"/escalations/{rid_a}/approve", headers=h).json()
    rb = client.post(f"/escalations/{rid_b}/approve", headers=h).json()
    assert ra["report_id"] == rid_a
    assert rb["report_id"] == rid_b
    assert rb.get("idempotent_replay") is not True  # distinct report -> fresh action


# ------------------------------------------------------------- websocket hardening
def test_ws_heartbeat_ping_on_idle(monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("DM_WS_PING", "0.1")
    from disastermind.api.app import create_app

    bus, service = _bare_service()
    client = _client(create_app(service))

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["kind"] == "snapshot"
        # No traffic published -> the next frame must be a server heartbeat ping.
        beat = ws.receive_json()
        assert beat["kind"] == "ping"
        assert "ts" in beat


def test_ws_concurrency_cap_closes_excess(monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("DM_WS_MAX", "1")
    from disastermind.api.app import create_app

    bus, service = _bare_service()
    client = _client(create_app(service))

    with client.websocket_connect("/ws") as ws1:
        assert ws1.receive_json()["kind"] == "snapshot"
        # At capacity: the second socket is accepted then immediately closed (1013).
        with client.websocket_connect("/ws") as ws2:
            event = ws2.receive()
            assert event["type"] == "websocket.close"
            assert event.get("code") == 1013


def test_ws_still_streams_after_hardening():
    """Back-compat: the existing live-stream behaviour is unchanged."""
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app

    bus, loop, service = _real_system()
    client = _client(create_app(service, loop=loop))

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["kind"] == "snapshot"
        bus.publish(_cross_state_field_order("inc-ws-stream"))
        seen = set()
        for _ in range(12):
            seen.add(ws.receive_json().get("topic"))
            if Topic.FIELD_ORDER in seen:
                break
        assert Topic.FIELD_ORDER in seen


# ----------------------------------------------------- existing routes unchanged
def test_existing_routes_still_behave():
    pytest.importorskip("fastapi")
    from disastermind.api.app import create_app

    bus, loop, service = _real_system()
    client = _client(create_app(service, loop=loop))

    assert client.get("/health").json()["status"] == "ok"
    assert isinstance(client.get("/topics").json(), dict)
    # Bare lists by default (no pagination params) — legacy shape preserved.
    assert isinstance(client.get("/incidents").json(), list)
    assert isinstance(client.get("/recent").json(), list)
    assert client.get("/escalations").json() == []
    # Security headers + request id still applied.
    resp = client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers.get("X-Request-ID")
    # Unknown escalation still 404s with the JSON envelope.
    nf = client.post("/escalations/nope/approve")
    assert nf.status_code == 404
    assert nf.json()["error"]["type"] == "http_error"
