"""Degraded-mode demo trigger endpoint (PRD Step 10 resilience) — offline.

The endpoint annotates simulated component failures for the dashboard's
resilience demo; the system stays operational throughout (degraded != down).
Needs FastAPI; skipped otherwise.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from disastermind.api.server import create_server  # noqa: E402


def _client() -> TestClient:
    return TestClient(create_server(start_streaming=False).app)


def test_status_starts_nominal():
    c = _client()
    s = c.get("/demo/status").json()
    assert s["degraded_components"] == [] and s["mode"] == "nominal"
    assert s["operational"] is True


def test_degrade_toggle_and_reset_keep_system_operational():
    c = _client()
    assert c.post("/demo/degrade?component=usgs&active=true").json()["degraded_components"] == ["usgs"]
    s = c.post("/demo/degrade?component=kafka&active=true").json()
    assert set(s["degraded_components"]) == {"usgs", "kafka"}
    assert s["operational"] is True and s["mode"] == "degraded"  # resilience: still up
    # clear one, then reset all
    assert c.post("/demo/degrade?component=usgs&active=false").json()["degraded_components"] == ["kafka"]
    assert c.post("/demo/degrade?reset=true").json()["degraded_components"] == []


def test_health_reflects_degradation_then_clears():
    c = _client()
    assert "degraded_components" not in c.get("/health").json()  # clean by default
    c.post("/demo/degrade?component=firms&active=true")
    assert c.get("/health").json().get("degraded_components") == ["firms"]
    c.post("/demo/degrade?reset=true")
    assert "degraded_components" not in c.get("/health").json()


def test_unknown_component_is_recorded_but_harmless():
    c = _client()
    # the endpoint doesn't reject free-form names (demo flexibility) but lists the
    # known set so the UI can offer real ones
    s = c.post("/demo/degrade?component=anything&active=true").json()
    assert "anything" in s["degraded_components"]
    assert "usgs" in s["known_components"]
