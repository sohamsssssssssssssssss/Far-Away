"""Graceful shutdown + rate-limit response headers for the served dashboard.

PRD Step 10 production hardening. Railway/k8s send ``SIGTERM`` on every redeploy,
so :class:`~disastermind.api.server.DashboardServer` must drain cleanly: stop the
coordination-loop driver, ask live ``/ws`` clients to close, and run registered
drain callbacks — without abruptly tearing down an in-flight disaster response.

These tests drive :meth:`DashboardServer.shutdown` **directly** (HARD RULE 2: no
real OS signals are raised, no wall-clock sleeps, no network). The rate-limit
header assertions need the FastAPI transport, so they ``importorskip`` it.

Covered:
  * :meth:`shutdown` is idempotent (drain runs exactly once);
  * :meth:`shutdown` stops the background loop driver;
  * registered drain callbacks run in registration order;
  * a drain callback that raises does not abort the rest of the drain;
  * default ``create_server`` arms NOTHING (no threads/handlers until ``run()``);
  * ``register_shutdown_callback`` rejects a non-callable / returns the callback;
  * ``X-RateLimit-*`` headers appear on an ALLOWED response under auth+limiting;
  * the same headers (+ ``Retry-After``) appear on a 429 when the bucket empties.
"""
from __future__ import annotations

import threading

import pytest

from disastermind.api.server import DashboardServer, create_server


# --------------------------------------------------------------------- helpers
def _fake_driver() -> threading.Thread:
    """A never-started thread carrying a ``_dm_stop`` Event, like the real driver.

    ``stop_loop_driver`` only sets ``thread._dm_stop`` and clears ``_driver``; it
    never joins, so an unstarted thread is a safe, deterministic stand-in (no
    background work, no wall-clock dependence).
    """
    stop = threading.Event()
    thread = threading.Thread(target=lambda: None, name="fake-driver", daemon=True)
    thread._dm_stop = stop  # type: ignore[attr-defined]
    thread.is_alive = lambda: True  # type: ignore[assignment]
    return thread


def _server() -> DashboardServer:
    """A wired server with streaming off (no listeners), no FastAPI needed."""
    return create_server(start_streaming=False)


# ----------------------------------------------------------- idempotency / drain
def test_shutdown_is_idempotent():
    server = _server()
    assert server.shutdown() is True  # first call performs the drain
    assert server.shutdown() is False  # second call is a no-op
    assert server.shutdown("again") is False


def test_shutdown_stops_the_loop_driver():
    server = _server()
    driver = _fake_driver()
    server._driver = driver
    stop = driver._dm_stop  # type: ignore[attr-defined]

    assert not stop.is_set()
    server.shutdown()
    # The driver was asked to stop and the handle was cleared.
    assert stop.is_set()
    assert server._driver is None


def test_shutdown_runs_callbacks_in_registration_order():
    server = _server()
    order: list[str] = []
    server.register_shutdown_callback(lambda: order.append("first"), name="first")
    server.register_shutdown_callback(lambda: order.append("second"), name="second")
    server.register_shutdown_callback(lambda: order.append("third"), name="third")

    server.shutdown()
    assert order == ["first", "second", "third"]


def test_shutdown_tolerates_a_callback_that_raises():
    server = _server()
    order: list[str] = []

    def boom() -> None:
        order.append("boom")
        raise RuntimeError("drain callback exploded")

    server.register_shutdown_callback(lambda: order.append("before"), name="before")
    server.register_shutdown_callback(boom, name="boom")
    server.register_shutdown_callback(lambda: order.append("after"), name="after")

    # The raising callback must NOT abort the drain — everything still runs.
    assert server.shutdown() is True
    assert order == ["before", "boom", "after"]


def test_shutdown_runs_callbacks_only_once():
    server = _server()
    calls: list[int] = []
    server.register_shutdown_callback(lambda: calls.append(1))

    server.shutdown()
    server.shutdown()  # idempotent: callback is not invoked a second time
    assert calls == [1]


def test_register_shutdown_callback_returns_callback_and_rejects_noncallable():
    server = _server()

    def cb() -> None:  # pragma: no cover - never invoked here
        pass

    assert server.register_shutdown_callback(cb) is cb  # usable as a decorator
    with pytest.raises(TypeError):
        server.register_shutdown_callback(object())  # type: ignore[arg-type]


def test_create_server_installs_no_threads_or_handlers():
    """Default behaviour unchanged: create_server() arms nothing (HARD RULE)."""
    server = _server()
    assert server._driver is None  # no background loop driver
    assert server._shutdown is None  # no GracefulShutdown / signal handlers armed
    assert server._shutdown_callbacks == []
    assert server._shutdown_done is False


def test_signal_ws_close_is_safe_without_a_built_app():
    """Asking WS clients to close before the FastAPI app exists is a no-op."""
    server = _server()
    assert server._app is None
    server._signal_ws_close()  # must not raise


# --------------------------------------------------------- WS close on shutdown
def test_shutdown_sets_the_ws_closing_event_when_app_built():
    pytest.importorskip("fastapi")
    server = _server()
    app = server.app  # build the FastAPI app (lazy)
    event = app.state.ws_closing
    assert isinstance(event, threading.Event)
    assert not event.is_set()

    server.shutdown()
    # Live /ws handlers poll this Event and close cleanly (1001) when it is set.
    assert event.is_set()


# ----------------------------------------------------- rate-limit response headers
def _client_with_auth(monkeypatch, *, capacity: int = 5):
    """A TestClient over an auth+rate-limited server (keys + a small bucket)."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DM_API_KEYS", "secret-token")
    # A small per-principal bucket so we can both observe headers on an allowed
    # request AND empty it to force a 429 deterministically (no clock/sleep).
    monkeypatch.setenv("DM_RATE_CAPACITY", str(capacity))
    monkeypatch.setenv("DM_RATE_REFILL_PER_SEC", "0")  # no refill: empties for good
    # Keep the per-IP outer bound generous so it never trips first.
    monkeypatch.setenv("DM_RATE_IP_CAPACITY", "10000")
    monkeypatch.setenv("DM_RATE_IP_REFILL_PER_SEC", "10000")
    server = create_server(start_streaming=False)
    return TestClient(server.app)


def test_ratelimit_headers_on_allowed_response(monkeypatch):
    client = _client_with_auth(monkeypatch, capacity=5)
    resp = client.get("/topics", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200

    # Standard X-RateLimit-* trio present on an allowed (200) response.
    assert resp.headers["X-RateLimit-Limit"] == "5"
    remaining = int(resp.headers["X-RateLimit-Remaining"])
    # One token spent by this request -> 4 of 5 remain.
    assert 0 <= remaining <= 5
    assert remaining == 4
    assert resp.headers["X-RateLimit-Reset"] == "0"  # tokens remain -> no wait


def test_ratelimit_headers_on_429(monkeypatch):
    # Capacity 1, no refill: the first call is allowed, the next is rate-limited.
    client = _client_with_auth(monkeypatch, capacity=1)
    auth = {"Authorization": "Bearer secret-token"}

    first = client.get("/topics", headers=auth)
    assert first.status_code == 200
    assert first.headers["X-RateLimit-Remaining"] == "0"

    blocked = client.get("/topics", headers=auth)
    assert blocked.status_code == 429
    # On a 429 the same X-RateLimit-* trio appears alongside Retry-After.
    assert blocked.headers["X-RateLimit-Limit"] == "1"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert int(blocked.headers["X-RateLimit-Reset"]) >= 1
    assert "Retry-After" in blocked.headers


def test_no_ratelimit_headers_when_auth_disabled(monkeypatch):
    """Default-open deployment is unchanged: no rate-limit middleware, no headers."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.delenv("DM_API_KEYS", raising=False)
    monkeypatch.delenv("DM_API_KEYS_MAP", raising=False)
    client = TestClient(create_server(start_streaming=False).app)
    resp = client.get("/topics")
    assert resp.status_code == 200
    assert "X-RateLimit-Limit" not in resp.headers
