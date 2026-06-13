"""`/validation/cyclone` route — serves the national cyclone backtest metrics.

Thin wrapper over hindcast.cyclone_backtest; same JSON the Evidence map renders.
Needs FastAPI; skipped otherwise.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from disastermind.api.server import create_server  # noqa: E402


def _client() -> TestClient:
    return TestClient(create_server(start_streaming=False).app)


def test_cyclone_route_returns_real_metrics():
    c = _client()
    r = c.get("/validation/cyclone")
    assert r.status_code == 200
    d = r.json()
    assert d["total_storms"] == 92
    assert d["regions"] and 0.0 <= d["activation_rate"] <= 1.0
    # honest 'unknown' accounting present, never inflated
    assert d["activated"] + d["unknown"] <= d["total_storms"]


def test_cyclone_route_versioned_alias_matches():
    c = _client()
    assert c.get("/v1/validation/cyclone").json() == c.get("/validation/cyclone").json()
