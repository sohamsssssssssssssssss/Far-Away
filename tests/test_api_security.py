"""Integration: optional security mount on the dashboard server (PRD Step 7).

Auth is OFF by default; when API keys are configured via the environment the
server enforces bearer-token auth on data routes while leaving the static UI and
/health open. Needs FastAPI (the transport); skipped otherwise.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from disastermind.api.server import create_server  # noqa: E402


def _client_with_keys(monkeypatch, keys="secret-token"):
    monkeypatch.setenv("DM_API_KEYS", keys)
    # build the app AFTER the env is set so TokenStore.from_env() sees the keys
    server = create_server(start_streaming=False)
    return TestClient(server.app)


def test_default_open_when_no_keys(monkeypatch):
    monkeypatch.delenv("DM_API_KEYS", raising=False)
    monkeypatch.delenv("DM_API_KEYS_MAP", raising=False)
    client = TestClient(create_server(start_streaming=False).app)
    # no keys configured -> every route is open
    assert client.get("/topics").status_code == 200
    assert client.get("/health").status_code == 200


def test_data_route_requires_token_when_configured(monkeypatch):
    client = _client_with_keys(monkeypatch)
    # no credentials -> 401 on a data route
    assert client.get("/topics").status_code == 401
    # valid bearer token -> allowed
    ok = client.get("/topics", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200
    # X-API-Key header is also accepted
    assert client.get("/topics", headers={"X-API-Key": "secret-token"}).status_code == 200
    # a wrong token is rejected
    assert client.get("/topics", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_health_and_ui_stay_open_under_auth(monkeypatch):
    client = _client_with_keys(monkeypatch)
    assert client.get("/health").status_code == 200      # health is always open
    assert client.get("/").status_code == 200            # static UI is always open


def test_env_isolated_after_tests(monkeypatch):
    # sanity: removing the key returns the server to open mode
    monkeypatch.delenv("DM_API_KEYS", raising=False)
    client = TestClient(create_server(start_streaming=False).app)
    assert client.get("/topics").status_code == 200
