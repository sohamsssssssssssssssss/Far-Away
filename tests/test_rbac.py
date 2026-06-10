"""RBAC scoped-token access control on the dashboard API (PRD Step 7).

Roles (viewer < operator < admin) are enforced by the transport layer ONLY when
role-bearing tokens are configured (``DM_API_KEYS_MAP`` with roles). A flat
``DM_API_KEYS`` store stays role-flat (everyone operator), and an unconfigured
deploy is fully open — so existing tests/clients are unaffected. Needs FastAPI.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from disastermind.api.server import create_server  # noqa: E402


def _client(monkeypatch, *, keys_map=None, keys=None):
    monkeypatch.delenv("DM_API_KEYS", raising=False)
    monkeypatch.delenv("DM_API_KEYS_MAP", raising=False)
    if keys_map:
        monkeypatch.setenv("DM_API_KEYS_MAP", keys_map)
    if keys:
        monkeypatch.setenv("DM_API_KEYS", keys)
    return TestClient(create_server(start_streaming=False).app)


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


# ------------------------------------------------------------------ RBAC enforced
def test_viewer_can_read_but_not_act(monkeypatch):
    client = _client(monkeypatch, keys_map="val:viewtok:viewer,op:optok:operator")
    # viewer GET allowed
    assert client.get("/topics", headers=_bearer("viewtok")).status_code == 200
    # viewer cannot approve -> 403 (insufficient scope)
    r = client.post("/escalations/none/approve", headers=_bearer("viewtok"))
    assert r.status_code == 403


def test_operator_can_act(monkeypatch):
    client = _client(monkeypatch, keys_map="val:viewtok:viewer,op:optok:operator")
    assert client.get("/topics", headers=_bearer("optok")).status_code == 200
    # operator may approve (404/400 for a missing id is fine — NOT 403)
    r = client.post("/escalations/none/approve", headers=_bearer("optok"))
    assert r.status_code != 403


def test_no_token_rejected_when_rbac_configured(monkeypatch):
    client = _client(monkeypatch, keys_map="op:optok:operator")
    assert client.get("/topics").status_code == 401  # no creds at all


# --------------------------------------------------------------- back-compat paths
def test_flat_keys_are_operator_flat(monkeypatch):
    """A plain DM_API_KEYS token (no role) acts as operator — back-compat."""
    client = _client(monkeypatch, keys="plaintok")
    assert client.get("/topics", headers=_bearer("plaintok")).status_code == 200
    r = client.post("/escalations/none/approve", headers=_bearer("plaintok"))
    assert r.status_code != 403  # operator can act


def test_unconfigured_is_fully_open(monkeypatch):
    client = _client(monkeypatch)  # no keys at all
    assert client.get("/topics").status_code == 200
    assert client.post("/escalations/none/approve").status_code != 401


# ------------------------------------------------------------ security headers
def test_csp_and_security_headers_present(monkeypatch):
    client = _client(monkeypatch)
    h = client.get("/").headers
    assert h.get("content-security-policy")
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"
