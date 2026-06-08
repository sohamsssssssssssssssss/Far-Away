"""Tests for the live dashboard *server* (PRD Step 7 dashboard + Step 10 WS).

:func:`disastermind.api.server.create_server` wires a real DisasterMind system
via :func:`disastermind.orchestration.build.build_system`, constructs a
:class:`~disastermind.api.service.DashboardService`, and returns a
:class:`DashboardServer` — all with the standard library alone (no uvicorn, and
no FastAPI unless the test explicitly opts in via ``pytest.importorskip``).

The static single-file UI is asserted to exist and to reference the ``/ws``
WebSocket and ``/topics`` polling endpoint, per the package contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from disastermind.api.server import (
    INDEX_HTML,
    STATIC_DIR,
    DashboardServer,
    create_server,
    run,
)
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


# ----------------------------------------------------------------- helpers
def _cross_state_field_order(incident_id: str = "usgs:eq-srv") -> Message:
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
            "orders": [{"team_id": "NDRF-99", "site": "cross-border", "priority": 1}],
            "escalation": {
                "trigger": EscalationTrigger.CROSS_STATE_RESOURCE.value,
                "summary": "needs a neighbouring state's NDRF battalion",
                "scale": 1,
            },
        },
    )


# ----------------------------------------------------------------- create_server
def test_create_server_wires_real_system_without_uvicorn():
    """create_server() builds a DashboardService over a real build_system DAG."""
    server = create_server()
    assert isinstance(server, DashboardServer)
    assert isinstance(server.service, DashboardService)
    # A full system was wired and exposes a live Commander to drive escalations.
    assert server.loop is not None
    assert server.loop.commander is not None
    assert server.service.commander is server.loop.commander
    # No uvicorn / FastAPI was needed to get here.
    assert server.service.health()["status"] == "ok"


def test_create_server_uses_supplied_infrastructure():
    bus = InMemoryBus()
    logger = DecisionLogger.null()
    settings = Settings()
    server = create_server(bus=bus, logger=logger, settings=settings)
    assert server.bus is bus
    assert server.service.bus is bus


def test_server_service_drives_escalation_lifecycle():
    """The wired service observes the bus and approves a real escalation."""
    server = create_server()
    bus, service = server.bus, server.service

    assert service.list_escalations() == []
    bus.publish(_cross_state_field_order())

    pending = service.list_escalations()
    assert len(pending) == 1
    report_id = pending[0]["report_id"]

    # Streaming is on by default so /ws would receive these.
    result = service.approve(report_id, approver="commander_jane")
    assert result["ok"] is True
    assert service.list_escalations() == []


def test_create_server_streaming_can_be_disabled():
    server = create_server(start_streaming=False)
    received: list[dict] = []
    server.service.add_listener(received.append)
    server.bus.publish(_cross_state_field_order("inc-nostream"))
    # Without start_streaming the service is not subscribed -> no fan-out.
    assert received == []


# ----------------------------------------------------------------- static UI
def test_static_index_exists():
    assert STATIC_DIR.is_dir()
    assert INDEX_HTML.is_file()
    assert INDEX_HTML == Path(STATIC_DIR) / "index.html"


def test_static_index_references_ws_and_topics():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "/ws" in html
    assert "/topics" in html
    # It must also wire the approve/reject POST endpoints (PRD Step 7).
    assert "/escalations" in html
    assert "approve" in html and "reject" in html


def test_index_html_helper_returns_source():
    html = DashboardServer.index_html()
    assert isinstance(html, str)
    assert "/ws" in html


# ----------------------------------------------------------------- run() guards
def test_server_run_raises_without_uvicorn(monkeypatch):
    """run() fails loudly if uvicorn is missing, but importing never needs it."""
    import builtins

    real_import = builtins.__import__

    def _no_uvicorn(name, *args, **kwargs):
        if name == "uvicorn" or name.startswith("uvicorn."):
            raise ImportError("simulated missing uvicorn")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_uvicorn)
    server = create_server()
    with pytest.raises(RuntimeError):
        server.run()


def test_module_run_helper_is_callable():
    # The module-level run() convenience exists and is callable (not invoked here
    # because that would block on a real server socket).
    assert callable(run)


# ----------------------------------------------------------------- FastAPI (optional)
def test_app_serves_static_index_and_endpoints():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    server = create_server()
    client = TestClient(server.app)

    # Static dashboard at "/" references the live endpoints.
    root = client.get("/")
    assert root.status_code == 200
    assert "/ws" in root.text and "/topics" in root.text

    # The real REST routes from api.app are mounted alongside.
    assert client.get("/topics").json() is not None
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/escalations").json() == []


def test_app_is_cached():
    pytest.importorskip("fastapi")
    server = create_server()
    assert server.app is server.app


def test_app_full_escalation_flow_over_http():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    server = create_server()
    client = TestClient(server.app)

    server.bus.publish(_cross_state_field_order("inc-http-srv"))
    escalations = client.get("/escalations").json()
    assert len(escalations) == 1
    report_id = escalations[0]["report_id"]

    resp = client.post(f"/escalations/{report_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert client.get("/escalations").json() == []
