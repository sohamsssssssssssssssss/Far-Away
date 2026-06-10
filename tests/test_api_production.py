"""Production-hardening tests for the deployed dashboard API (PRD Step 9/10).

Covers the operational surface added on top of the existing transport:

  * ``/healthz`` (liveness) + ``/readyz`` (readiness) + ``/metrics`` respond;
  * security headers present on responses (and HSTS only behind TLS);
  * a per-request ``X-Request-ID`` is generated and echoed (and an inbound one
    is honoured);
  * errors return the consistent JSON envelope
    ``{"error": {"type", "detail", "request_id"}}``;
  * pagination: the list endpoints stay bare lists by default but switch to the
    ``{"items", "total", "limit", "offset"}`` envelope when ``?limit=/?offset=``
    is supplied, and ``limit`` actually limits;
  * :func:`create_server` starts NO background thread (tests stay deterministic);
    the loop driver only runs in the serving path and is opt-out via the env.

FastAPI is optional (HARD RULE 2), so the transport tests are guarded by
``pytest.importorskip("fastapi")``; the thread-free assertions need only stdlib.
"""
from __future__ import annotations

import threading

import pytest

from disastermind.api.server import create_server
from disastermind.core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)


# ----------------------------------------------------------------- helpers
def _cross_state_field_order(incident_id: str = "usgs:eq-prod") -> Message:
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


def _client(**kw):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    server = create_server(start_streaming=False)
    return server, TestClient(server.app, **kw)


# ------------------------------------------------ create_server: no threads
def test_create_server_starts_no_background_thread(monkeypatch):
    """Determinism (HARD RULE 2): create_server must not spawn a driver thread."""
    monkeypatch.delenv("DM_API_DRIVE_LOOP", raising=False)
    before = threading.active_count()
    server = create_server()
    assert threading.active_count() == before, "create_server spawned a thread"
    assert server._driver is None
    # No dm-api-loop-driver thread exists anywhere.
    assert not any(t.name == "dm-api-loop-driver" for t in threading.enumerate())


def test_loop_driver_opt_out_via_env(monkeypatch):
    """DM_API_DRIVE_LOOP=0 disables the driver even when explicitly started."""
    monkeypatch.setenv("DM_API_DRIVE_LOOP", "0")
    server = create_server(start_streaming=False)
    assert server.start_loop_driver() is False
    assert server._driver is None


def test_loop_driver_seeds_and_ticks_real_data(monkeypatch):
    """The driver seeds teams once and ticks run_once so live data flows."""
    monkeypatch.setenv("DM_API_DRIVE_LOOP", "1")
    monkeypatch.setenv("DM_API_TICK", "0.02")
    server = create_server(start_streaming=False)
    before = len(server.bus.history)
    assert server.start_loop_driver() is True
    try:
        # Wait until the driver has produced live traffic (bounded, deterministic).
        deadline = 0
        while len(server.bus.history) <= before and deadline < 200:
            import time

            time.sleep(0.01)
            deadline += 1
        assert len(server.bus.history) > before, "loop driver produced no live data"
        assert server.loop.cycle >= 1
    finally:
        server.stop_loop_driver()
    assert server._driver is None


# --------------------------------------------------- health / ready / metrics
def test_healthz_is_always_live():
    _, client = _client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "alive"
    assert body["live"] is True


def test_readyz_reports_readiness():
    _, client = _client()
    resp = client.get("/readyz")
    # A fully wired system is ready -> 200; a degraded one would be 503. Either
    # way the body carries a structured readiness report.
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "ready" in body and "status" in body and "checks" in body
    assert resp.status_code == (200 if body["ready"] else 503)


def test_readyz_not_ready_without_loop():
    """With no wired loop the readiness probe degrades to 503, never crashes."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from disastermind.api.app import create_app
    from disastermind.api.service import DashboardService
    from disastermind.core.bus import InMemoryBus
    from disastermind.tier1.commander.agent import CommanderAgent

    bus = InMemoryBus()
    commander = CommanderAgent(bus=bus)
    service = DashboardService(bus=bus, commander=commander)
    app = create_app(service, loop=None, collector=None)
    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False


def test_metrics_prometheus_exposition():
    server, client = _client()
    # Generate some traffic so the collector has something to render.
    server.bus.publish(_cross_state_field_order("inc-metrics"))
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    text = resp.text
    assert "# HELP disastermind_messages_total" in text
    assert "# TYPE disastermind_messages_total counter" in text


def test_metrics_empty_but_valid_without_collector():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from disastermind.api.app import create_app
    from disastermind.api.service import DashboardService
    from disastermind.core.bus import InMemoryBus
    from disastermind.tier1.commander.agent import CommanderAgent

    bus = InMemoryBus()
    service = DashboardService(bus=bus, commander=CommanderAgent(bus=bus))
    app = create_app(service, collector=None)
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.text == ""


# --------------------------------------------------------- security headers
def test_security_headers_present():
    _, client = _client()
    resp = client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in resp.headers
    # Plain HTTP must NOT assert HSTS.
    assert "Strict-Transport-Security" not in resp.headers


def test_hsts_only_behind_tls():
    _, client = _client()
    resp = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert "Strict-Transport-Security" in resp.headers
    assert "max-age=" in resp.headers["Strict-Transport-Security"]


# ----------------------------------------------------------- request id
def test_request_id_generated_and_echoed():
    _, client = _client()
    resp = client.get("/health")
    rid = resp.headers.get("X-Request-ID")
    assert rid, "no X-Request-ID generated"
    # Two requests get distinct ids.
    rid2 = client.get("/health").headers.get("X-Request-ID")
    assert rid2 and rid2 != rid


def test_inbound_request_id_is_honoured():
    _, client = _client()
    resp = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers.get("X-Request-ID") == "trace-abc-123"


# ----------------------------------------------------------- error envelope
def test_error_returns_json_envelope():
    _, client = _client()
    resp = client.post("/escalations/does-not-exist/approve")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert err["type"] == "http_error"
    assert "does-not-exist" in err["detail"]
    # The envelope carries the same request id echoed in the header.
    assert err["request_id"] == resp.headers.get("X-Request-ID")


def test_unhandled_error_returns_500_envelope():
    """A route that raises an unexpected error still yields the JSON envelope."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    server = create_server(start_streaming=False)
    app = server.app

    @app.get("/_boom_test")
    def _boom() -> dict:
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/_boom_test")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["type"] == "internal_error"
    assert body["error"]["request_id"] == resp.headers.get("X-Request-ID")


# ------------------------------------------------------------- pagination
def test_list_endpoints_are_bare_lists_by_default():
    """Backward compatible: no query params -> the legacy bare-array shape."""
    _, client = _client()
    assert isinstance(client.get("/incidents").json(), list)
    assert isinstance(client.get("/escalations").json(), list)
    assert isinstance(client.get("/recent").json(), list)


def test_pagination_limits_results_with_envelope():
    server, client = _client()
    # Produce several distinct incidents on the bus.
    for i in range(5):
        server.bus.publish(_cross_state_field_order(f"inc-page-{i}"))

    full = client.get("/incidents").json()
    assert isinstance(full, list)
    assert len(full) >= 5

    paged = client.get("/incidents", params={"limit": 2}).json()
    assert isinstance(paged, dict)
    assert set(paged) == {"items", "total", "limit", "offset"}
    assert paged["limit"] == 2
    assert paged["offset"] == 0
    assert paged["total"] == len(full)
    assert len(paged["items"]) == 2

    # Offset advances the window.
    page2 = client.get("/incidents", params={"limit": 2, "offset": 2}).json()
    assert page2["offset"] == 2
    assert page2["items"] == full[2:4]


def test_recent_pagination_envelope():
    server, client = _client()
    server.bus.publish(_cross_state_field_order("inc-recent-page"))
    paged = client.get("/recent", params={"limit": 1}).json()
    assert isinstance(paged, dict)
    assert paged["limit"] == 1
    assert len(paged["items"]) <= 1
    assert paged["total"] >= 1


def test_escalations_pagination_envelope():
    server, client = _client()
    server.bus.publish(_cross_state_field_order("inc-esc-page"))
    # Default (no params) is a bare list with exactly one open escalation.
    assert len(client.get("/escalations").json()) == 1
    paged = client.get("/escalations", params={"limit": 10, "offset": 0}).json()
    assert paged["total"] == 1
    assert len(paged["items"]) == 1
