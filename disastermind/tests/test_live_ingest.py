"""Live-ingest wiring tests (PRD Step 2 / Step 9-10) — fully offline.

Verifies :func:`disastermind.live.poll_feeds` drives the Tier-3 ingestion
adapters into the runtime, emitting :data:`Topic.RAW_FEED` from the chosen
source:

  * ``live=True`` with an *injected* recorded-USGS transport produces a RAW_FEED
    message with **no real network** (the socket is never opened — a stub returns
    the committed GeoJSON fixture);
  * ``live=False`` (the DEFAULT) uses each adapter's offline ``sample()``;
  * the ``LiveSystem`` opt-in switch stays OFF by default so the existing offline
    runtime is unchanged and the loop still reaches ``DISPATCH``.

Stdlib-only, deterministic, no network (HARD RULE 2). ``test_live_runtime.py``
stays green — this file only adds behaviour and never touches that path.
"""
from __future__ import annotations

import json
from pathlib import Path

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import Message, MessageType, Module, Priority, Topic
from disastermind.live import LiveSystem, poll_feed, poll_feeds
from disastermind.tier3.ingestion import seismic as seismic_mod
from disastermind.tier3.ingestion.build import build_agents
from disastermind.tier3.ingestion.seismic import USGSFeedAgent

FIXTURES = Path(seismic_mod.__file__).parent / "fixtures"


# ----------------------------------------------------------------- test doubles
class _Loop:
    """Minimal CoordinationLoop-like holder exposing ``.agents`` and a bus."""

    def __init__(self, agents, bus):
        self.agents = list(agents)
        self.bus = bus


def _recording_transport(text: str, status: int = 200):
    """A transport stub that records calls and returns canned text (no network)."""
    calls: list[tuple[str, float]] = []

    def transport(url: str, timeout: float):
        calls.append((url, timeout))
        return status, text

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def _usgs_fixture_text() -> str:
    return (FIXTURES / "usgs_all_hour.geojson").read_text(encoding="utf-8")


def _raw_feed_msgs(bus) -> list[Message]:
    return [m for m in bus.history if m.topic == Topic.RAW_FEED]


def _build_feed_loop():
    """Build a loop carrying only the real Tier-3 ingestion agents on one bus."""
    bus = InMemoryBus()
    agents = build_agents(bus, DecisionLogger.null(), Settings())
    return _Loop(agents, bus), bus


# ============================================================ live (injected stub)
def test_poll_feeds_live_with_injected_usgs_transport_emits_raw_feed_no_network():
    """live=True + injected USGS transport => RAW_FEED, never a real socket."""
    loop, bus = _build_feed_loop()
    transport = _recording_transport(_usgs_fixture_text())

    emitted = poll_feeds(loop, live=True, transport=transport)

    # The injected transport was actually exercised (else we'd have hit network).
    assert transport.calls, "live poll never used the injected transport"
    assert emitted >= 1

    raw = _raw_feed_msgs(bus)
    assert raw, "poll_feeds(live=True) produced no RAW_FEED message"
    assert all(m.topic == Topic.RAW_FEED for m in raw)

    # The USGS feed must have ingested from the *fixture* (M5.3/M4.7), so its
    # message carries observations decoded from the recorded GeoJSON, not from
    # the offline sample() (which is M4.9 near Guwahati).
    usgs = [m for m in raw if m.payload.get("kind") == "usgs"]
    assert usgs, "no USGS RAW_FEED emitted"
    obs = usgs[0].payload["observations"]
    mags = sorted(o["magnitude"] for o in obs)
    assert 5.3 in mags  # came from the recorded fixture, not sample()
    # M4.5+ present => an ALERT with a minted earthquake event.
    assert usgs[0].type is MessageType.ALERT
    assert usgs[0].payload["event"] is not None
    assert usgs[0].module is Module.EARTHQUAKE


def test_poll_feed_single_agent_live_uses_injected_transport():
    """The single-agent helper drives one feed's fetch()->parse() via the stub."""
    bus = InMemoryBus()
    agent = USGSFeedAgent(bus=bus, logger=DecisionLogger.null(), settings=Settings())
    transport = _recording_transport(_usgs_fixture_text())

    n = poll_feed(agent, live=True, transport=transport)

    assert n == 1
    assert transport.calls
    raw = _raw_feed_msgs(bus)
    assert len(raw) == 1
    assert raw[0].payload["kind"] == "usgs"
    assert 5.3 in {o["magnitude"] for o in raw[0].payload["observations"]}


def test_poll_feeds_live_transport_error_degrades_to_sample_no_raise():
    """A failing live transport degrades to sample() and still emits (Step 10)."""
    loop, bus = _build_feed_loop()
    bad = _recording_transport("", status=503)

    emitted = poll_feeds(loop, live=True, transport=bad)

    # No exception, feeds still produced RAW_FEED from their sample() fallback.
    assert emitted >= 1
    raw = _raw_feed_msgs(bus)
    usgs = [m for m in raw if m.payload.get("kind") == "usgs"]
    assert usgs, "USGS feed did not degrade-emit on transport error"
    # sample() carries the M4.9 Guwahati quake, not the fixture's M5.3.
    mags = {o["magnitude"] for o in usgs[0].payload["observations"]}
    assert 4.9 in mags and 5.3 not in mags


# ====================================================== offline (sample) default
def test_poll_feeds_default_uses_sample_path_no_transport_needed():
    """live=False (DEFAULT) emits RAW_FEED from sample() — no transport/network."""
    loop, bus = _build_feed_loop()

    emitted = poll_feeds(loop)  # default live=False, transport=None

    assert emitted >= 1
    raw = _raw_feed_msgs(bus)
    assert raw, "default poll_feeds produced no RAW_FEED"
    usgs = [m for m in raw if m.payload.get("kind") == "usgs"]
    assert usgs
    # The offline sample() M4.9 quake, deterministic and network-free.
    mags = {o["magnitude"] for o in usgs[0].payload["observations"]}
    assert 4.9 in mags


def test_poll_feeds_accepts_bare_agent_list():
    """poll_feeds works on a plain list of agents, not only a loop."""
    bus = InMemoryBus()
    agents = build_agents(bus, DecisionLogger.null(), Settings())

    emitted = poll_feeds(agents)  # offline default

    assert emitted >= 1
    assert _raw_feed_msgs(bus)


def test_poll_feeds_ignores_non_feed_agents():
    """Decision-authority agents are never treated as pollable feeds."""

    class _Decider:
        decision_authority = True
        name = "tier2.something"

        def emit(self, msg):  # pragma: no cover - must never be called as a feed
            raise AssertionError("non-feed agent was polled")

    bus = InMemoryBus()
    feeds = build_agents(bus, DecisionLogger.null(), Settings())
    mixed = [_Decider()] + feeds

    emitted = poll_feeds(mixed)  # offline; the decider must be skipped

    assert emitted >= 1
    assert _raw_feed_msgs(bus)


def test_poll_feeds_empty_input_returns_zero():
    assert poll_feeds([]) == 0
    assert poll_feeds(None) == 0


# ====================================================== LiveSystem opt-in switch
def test_livesystem_default_has_live_feeds_off():
    system = LiveSystem.build()
    assert system.live_feeds is False
    assert system.meta["live_feeds"] is False


def test_livesystem_poll_live_defaults_offline_emits_raw_feed():
    """LiveSystem.poll_live() with the default (off) switch uses sample()."""
    system = LiveSystem.build()
    before = len(_raw_feed_msgs(system.loop.bus))

    emitted = system.poll_live()  # effective_live = live_feeds = False

    assert emitted >= 1
    after = _raw_feed_msgs(system.loop.bus)
    assert len(after) == before + emitted
    # USGS rode the offline sample() (M4.9), never the network.
    usgs = [m for m in after if m.payload.get("kind") == "usgs"]
    assert usgs and 4.9 in {o["magnitude"] for o in usgs[-1].payload["observations"]}


def test_livesystem_poll_live_explicit_live_with_injected_transport():
    """poll_live(live=True, transport=stub) drives the fetch path, no network."""
    system = LiveSystem.build()
    transport = _recording_transport(_usgs_fixture_text())

    emitted = system.poll_live(live=True, transport=transport)

    assert emitted >= 1
    assert transport.calls
    usgs = [m for m in _raw_feed_msgs(system.loop.bus) if m.payload.get("kind") == "usgs"]
    assert usgs and 5.3 in {o["magnitude"] for o in usgs[-1].payload["observations"]}


def test_livesystem_build_live_feeds_flag_sets_default_source():
    """build(live_feeds=True) makes poll_live() default to the live source."""
    system = LiveSystem.build(live_feeds=True)
    assert system.live_feeds is True
    assert system.meta["live_feeds"] is True

    transport = _recording_transport(_usgs_fixture_text())
    # No explicit live= → uses live_feeds=True; transport still injected (no net).
    emitted = system.poll_live(transport=transport)
    assert emitted >= 1
    assert transport.calls


# ===================================================== existing runtime unchanged
def test_livesystem_default_still_reaches_dispatch():
    """The default offline runtime is unchanged: the DAG still reaches DISPATCH."""
    system = LiveSystem.build()

    readings = [
        {"team_id": "BOAT-01", "asset_type": "boat",
         "location": {"lat": 20.27, "lon": 85.84}, "status": "idle"},
        {"team_id": "NDRF-01", "asset_type": "ndrf_team",
         "location": {"lat": 20.30, "lon": 85.82}, "status": "idle"},
        {"team_id": "MED-01", "asset_type": "medical_unit",
         "location": {"lat": 20.29, "lon": 85.83}, "status": "idle"},
        {"team_id": "HELI-01", "asset_type": "helicopter",
         "location": {"lat": 20.24, "lon": 85.81}, "status": "idle"},
        {"team_id": "USAR-01", "asset_type": "usar_team",
         "location": {"lat": 20.31, "lon": 85.86}, "status": "idle"},
        {"team_id": "FIRE-01", "asset_type": "fire_engine",
         "location": {"lat": 20.28, "lon": 85.85}, "status": "idle"},
    ]
    system.loop.bus.publish(
        Message(
            sender="iot.gps_beacon",
            recipient="broadcast",
            type=MessageType.QUERY,
            priority=Priority.INFO,
            topic=Topic.IOT_TELEMETRY,
            module=Module.ALL,
            payload={"kind": "gps_beacon", "readings": readings},
        )
    )
    system.run_once(now_epoch=1000.0)
    system.run_once(now_epoch=1000.0)

    seen = {m.topic for m in system.loop.bus.history}
    assert Topic.DISPATCH in seen
    assert system.live_feeds is False  # the opt-in switch never flipped on
