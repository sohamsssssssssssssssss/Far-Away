"""WebSocket auth via query-param token (PRD Step 7 hardening).

Browsers cannot set an ``Authorization`` header on a WebSocket, so when
``DM_API_KEYS`` is configured the ``/ws`` stream must accept the token from the
query string (``/ws?token=…``) — otherwise the dashboard's live feed breaks the
moment auth is turned on. Needs FastAPI; skipped otherwise.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from disastermind.api.server import create_server  # noqa: E402

TOKEN = "secret-ws-token"


def _client(monkeypatch):
    monkeypatch.setenv("DM_API_KEYS", TOKEN)
    return TestClient(create_server(start_streaming=False).app)


def test_ws_requires_token_and_accepts_query_param(monkeypatch):
    client = _client(monkeypatch)
    # no token -> rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    # query-param token -> connects + streams the snapshot frame
    with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        first = ws.receive_json()
        assert first.get("kind") == "snapshot"
    # wrong token -> rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=nope") as ws:
            ws.receive_json()


def test_rest_still_works_with_header_and_query_param(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/topics").status_code == 401  # no creds
    assert client.get("/topics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get(f"/topics?token={TOKEN}").status_code == 200  # query-param fallback


def test_open_when_unconfigured(monkeypatch):
    monkeypatch.delenv("DM_API_KEYS", raising=False)
    client = TestClient(create_server(start_streaming=False).app)
    with client.websocket_connect("/ws") as ws:  # no auth -> open
        assert ws.receive_json().get("kind") == "snapshot"
