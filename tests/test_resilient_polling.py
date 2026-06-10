"""Resilient live-feed polling tests (PRD Step 10) — fully offline.

Exercises :func:`disastermind.live.resilient_poll_feeds`, the hardened wrapper
around :func:`disastermind.live.poll_feeds`:

  * a repeatedly-failing live feed trips its per-feed circuit breaker after the
    failure threshold, and subsequent polls *short-circuit* (the transport is no
    longer touched) — degrading to the offline ``sample()`` instead of hammering;
  * after the breaker's ``reset_timeout`` cooldown elapses (driven by an injected
    clock) it probes again, and a now-healthy feed recovers (breaker closes);
  * identical consecutive observation batches from one feed are de-duped (no
    duplicate RAW_FEED), while a *changed* batch is emitted;
  * the ``live=False`` DEFAULT is byte-for-byte unchanged — it delegates to
    ``poll_feeds`` and arms no breakers, so the offline path is inert.

Everything is stdlib-only, deterministic, and **never opens a socket**: feed
fetches are driven by an injected ``(url, timeout) -> (status, text)`` transport
stub, and time is an injected mutable clock (HARD RULE 2).
"""
from __future__ import annotations

import json
from pathlib import Path

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import Message, MessageType, Topic
from disastermind.live import poll_feeds, resilient_poll_feeds
from disastermind.live.resilient import (
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_RESET_TIMEOUT,
    _batch_hash,
)
from disastermind.tier3.ingestion import seismic as seismic_mod
from disastermind.tier3.ingestion.seismic import USGSFeedAgent

FIXTURES = Path(seismic_mod.__file__).parent / "fixtures"


# ----------------------------------------------------------------- test doubles
class _Clock:
    """A mutable injectable monotonic clock (no wall-clock)."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


def _usgs_fixture_text() -> str:
    return (FIXTURES / "usgs_all_hour.geojson").read_text(encoding="utf-8")


def _ok_transport(text: str):
    """A recording transport that always returns ``(200, text)`` (no network)."""
    calls: list[tuple[str, float]] = []

    def transport(url: str, timeout: float):
        calls.append((url, timeout))
        return 200, text

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def _failing_transport(status: int = 503):
    """A recording transport that always returns a non-2xx status (no network)."""
    calls: list[tuple[str, float]] = []

    def transport(url: str, timeout: float):
        calls.append((url, timeout))
        return status, ""

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def _raising_net_transport():
    """A recording transport that *raises* (simulating a network error)."""
    calls: list[tuple[str, float]] = []

    def transport(url: str, timeout: float):
        calls.append((url, timeout))
        raise OSError("connection refused")

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def _usgs_agent():
    bus = InMemoryBus()
    agent = USGSFeedAgent(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    return agent, bus


def _raw(bus) -> list[Message]:
    return [m for m in bus.history if m.topic == Topic.RAW_FEED]


def _usgs_raw(bus) -> list[Message]:
    return [m for m in _raw(bus) if m.payload.get("kind") == "usgs"]


# ============================================================ offline default ===
def test_default_offline_is_inert_and_matches_poll_feeds():
    """live=False delegates to poll_feeds and arms no breakers (unchanged path)."""
    agent_a, bus_a = _usgs_agent()
    agent_b, bus_b = _usgs_agent()

    # A persistent breakers dict is supplied but must be left untouched offline.
    breakers: dict = {}
    n_res = resilient_poll_feeds([agent_a], live=False, breakers=breakers)
    n_plain = poll_feeds([agent_b], live=False)

    assert n_res == n_plain >= 1
    assert breakers == {}, "offline default must not arm any breaker / state"

    # Both produced the offline sample() M4.9 quake — identical, no network.
    mags_res = {o["magnitude"] for o in _usgs_raw(bus_a)[0].payload["observations"]}
    mags_plain = {o["magnitude"] for o in _usgs_raw(bus_b)[0].payload["observations"]}
    assert mags_res == mags_plain
    assert 4.9 in mags_res and 5.3 not in mags_res


def test_default_offline_no_transport_no_clock_needed():
    agent, bus = _usgs_agent()
    assert resilient_poll_feeds([agent]) >= 1  # all-defaults: live=False
    assert _usgs_raw(bus)


def test_empty_and_none_inputs_return_zero():
    assert resilient_poll_feeds([], live=True) == 0
    assert resilient_poll_feeds(None, live=True) == 0
    assert resilient_poll_feeds([]) == 0


# ====================================================== live success / no network
def test_live_success_emits_fixture_batch_via_injected_transport():
    """live=True + healthy transport => RAW_FEED from the fixture, no socket."""
    agent, bus = _usgs_agent()
    transport = _ok_transport(_usgs_fixture_text())
    clock = _Clock()

    emitted = resilient_poll_feeds(
        [agent], live=True, transport=transport, breakers={}, clock=clock
    )

    assert emitted == 1
    assert transport.calls, "live poll never used the injected transport"
    obs = _usgs_raw(bus)[0].payload["observations"]
    mags = {o["magnitude"] for o in obs}
    assert 5.3 in mags  # from the recorded fixture, not sample()'s M4.9
    assert _usgs_raw(bus)[0].type is MessageType.ALERT


# ====================================================== breaker opens & short-circuits
def test_breaker_opens_after_threshold_and_short_circuits():
    """A repeatedly-failing feed trips its breaker; later polls stop hitting net."""
    agent, bus = _usgs_agent()
    transport = _failing_transport(503)
    clock = _Clock()
    breakers: dict = {}

    # Poll exactly ``failure_threshold`` times: each live fetch fails (503),
    # degrades to sample(), and records a breaker failure.
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        resilient_poll_feeds(
            [agent], live=True, transport=transport, breakers=breakers, clock=clock
        )

    calls_after_trip = len(transport.calls)
    assert calls_after_trip >= DEFAULT_FAILURE_THRESHOLD, "transport should have been hit each cycle"

    # The breaker for this feed must now be OPEN.
    breaker = breakers[agent.name]
    assert breaker.is_open, "breaker did not OPEN after the failure threshold"

    # Subsequent polls SHORT-CIRCUIT: the transport is no longer touched at all.
    for _ in range(5):
        resilient_poll_feeds(
            [agent], live=True, transport=transport, breakers=breakers, clock=clock
        )
    assert len(transport.calls) == calls_after_trip, (
        "OPEN breaker still hammered the transport instead of short-circuiting"
    )

    # Throughout, the runtime degraded to sample() and kept emitting (de-duped):
    # at least the first cycle produced a RAW_FEED from sample().
    usgs = _usgs_raw(bus)
    assert usgs, "no degraded RAW_FEED emitted while breaker tripped"
    assert 4.9 in {o["magnitude"] for o in usgs[0].payload["observations"]}


def test_breaker_opens_on_transport_exceptions_too():
    """A transport that *raises* (network error) also trips the breaker."""
    agent, _bus = _usgs_agent()
    transport = _raising_net_transport()
    clock = _Clock()
    breakers: dict = {}

    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        resilient_poll_feeds(
            [agent], live=True, transport=transport, breakers=breakers, clock=clock
        )

    assert breakers[agent.name].is_open
    n_before = len(transport.calls)
    resilient_poll_feeds(
        [agent], live=True, transport=transport, breakers=breakers, clock=clock
    )
    assert len(transport.calls) == n_before  # short-circuited


def test_breaker_recovers_after_cooldown_with_healthy_transport():
    """After reset_timeout the breaker probes; a healthy feed closes it again."""
    agent, bus = _usgs_agent()
    bad = _failing_transport(503)
    clock = _Clock()
    breakers: dict = {}

    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        resilient_poll_feeds(
            [agent], live=True, transport=bad, breakers=breakers, clock=clock
        )
    assert breakers[agent.name].is_open

    # Advance past the cooldown and poll with a now-healthy transport.
    clock.advance(DEFAULT_RESET_TIMEOUT + 1.0)
    good = _ok_transport(_usgs_fixture_text())
    resilient_poll_feeds(
        [agent], live=True, transport=good, breakers=breakers, clock=clock
    )

    # The breaker probed (half-open) and, on success, closed.
    assert breakers[agent.name].is_closed
    assert good.calls, "breaker did not probe the transport after cooldown"
    # The recovered live batch (fixture M5.3) was emitted.
    assert 5.3 in {o["magnitude"] for o in _usgs_raw(bus)[-1].payload["observations"]}


# ====================================================== consecutive-batch de-dup
def test_identical_consecutive_batches_are_deduped():
    """The same live batch twice in a row emits exactly one RAW_FEED."""
    agent, bus = _usgs_agent()
    transport = _ok_transport(_usgs_fixture_text())
    clock = _Clock()
    breakers: dict = {}

    n1 = resilient_poll_feeds(
        [agent], live=True, transport=transport, breakers=breakers, clock=clock
    )
    n2 = resilient_poll_feeds(
        [agent], live=True, transport=transport, breakers=breakers, clock=clock
    )

    assert n1 == 1
    assert n2 == 0, "identical consecutive batch was not de-duped"
    assert len(_usgs_raw(bus)) == 1


def test_changed_batch_after_identical_is_emitted():
    """A *different* batch following an identical one is emitted, not suppressed."""
    agent, bus = _usgs_agent()
    fixture = _usgs_fixture_text()
    transport = _ok_transport(fixture)
    clock = _Clock()
    breakers: dict = {}

    resilient_poll_feeds([agent], live=True, transport=transport, breakers=breakers, clock=clock)
    resilient_poll_feeds([agent], live=True, transport=transport, breakers=breakers, clock=clock)
    assert len(_usgs_raw(bus)) == 1  # second was a dup

    # Now mutate the fixture (drop one feature) so the batch content changes.
    data = json.loads(fixture)
    data["features"] = data["features"][:1]
    transport2 = _ok_transport(json.dumps(data))
    n3 = resilient_poll_feeds(
        [agent], live=True, transport=transport2, breakers=breakers, clock=clock
    )

    assert n3 == 1, "a changed batch must be emitted, not de-duped"
    assert len(_usgs_raw(bus)) == 2


def test_batch_hash_is_deterministic_and_order_independent():
    a = [{"id": "x", "magnitude": 5.3}, {"id": "y", "magnitude": 2.1}]
    b = [{"magnitude": 5.3, "id": "x"}, {"magnitude": 2.1, "id": "y"}]
    c = [{"id": "x", "magnitude": 5.4}, {"id": "y", "magnitude": 2.1}]
    assert _batch_hash(a) == _batch_hash(b)
    assert _batch_hash(a) != _batch_hash(c)


# ====================================================== per-feed isolation
def test_per_feed_breakers_are_independent():
    """One feed tripping its breaker must not short-circuit a healthy sibling."""
    bad_agent, bad_bus = _usgs_agent()
    good_agent, good_bus = _usgs_agent()
    # Distinguish the two same-class agents by name so their breakers are keyed apart.
    bad_agent.name = "ingest.usgs.bad"
    good_agent.name = "ingest.usgs.good"

    clock = _Clock()
    breakers: dict = {}

    # The shared transport fails for the "bad" feed's URL pattern only by virtue
    # of the test using a single transport that fails — instead, drive them in
    # separate calls so each gets its own behaviour deterministically.
    bad_t = _failing_transport(503)
    good_t = _ok_transport(_usgs_fixture_text())

    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        resilient_poll_feeds(
            [bad_agent], live=True, transport=bad_t, breakers=breakers, clock=clock
        )
    assert breakers["ingest.usgs.bad"].is_open

    # The good feed, polled with the same breakers dict, is unaffected.
    n = resilient_poll_feeds(
        [good_agent], live=True, transport=good_t, breakers=breakers, clock=clock
    )
    assert n == 1
    assert "ingest.usgs.good" not in breakers or breakers["ingest.usgs.good"].is_closed
    assert good_t.calls, "healthy sibling feed was wrongly short-circuited"


# ====================================================== ops-absent fallback
def test_falls_back_to_poll_feeds_when_ops_absent(monkeypatch):
    """If ops import fails on the live path, degrade to a plain live poll."""

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "disastermind.ops" or name.endswith(".ops"):
            raise ImportError("ops unavailable (simulated)")
        return real_import(name, *args, **kwargs)

    # Patch the builtins import used by the lazy ``from ..ops import ...``.
    monkeypatch.setattr("builtins.__import__", fake_import)

    agent, bus = _usgs_agent()
    transport = _ok_transport(_usgs_fixture_text())
    # Must not raise; falls back to poll_feeds(live=True) which still emits.
    emitted = resilient_poll_feeds([agent], live=True, transport=transport)
    assert emitted >= 1
    assert _usgs_raw(bus)
