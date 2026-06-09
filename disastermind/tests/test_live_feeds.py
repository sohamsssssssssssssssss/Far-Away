"""Live Tier-3 feed connectors (PRD Step 2) — fully offline.

The live ``fetch()`` path is validated WITHOUT touching the network: parsing is
exercised against committed real-API fixtures, and ``fetch`` is driven through an
injected transport stub (so the real socket is never opened in a test). Also
verifies graceful degradation to ``sample()`` on a transport error (PRD Step 10).
"""
from __future__ import annotations

import json
from pathlib import Path

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.tier3.ingestion import seismic as seismic_mod
from disastermind.tier3.ingestion.openmeteo import OpenMeteoFeedAgent
from disastermind.tier3.ingestion.seismic import USGSFeedAgent
from disastermind.tier3.ingestion.wildfire import FIRMSFeedAgent

FIXTURES = Path(seismic_mod.__file__).parent / "fixtures"


def _agent(cls):
    return cls(bus=InMemoryBus(), logger=DecisionLogger.null(), settings=Settings())


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _recording_transport(text: str, status: int = 200):
    """A transport stub that records calls and returns canned bytes (no network)."""
    calls: list[tuple[str, float]] = []

    def transport(url: str, timeout: float):
        calls.append((url, timeout))
        return status, text

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


# --------------------------------------------------------------- fixtures exist
def test_fixtures_present():
    for name in ("usgs_all_hour.geojson", "open_meteo_forecast.json", "firms_viirs.csv"):
        assert (FIXTURES / name).is_file(), f"missing recorded fixture {name}"


# ------------------------------------------------------------------- parsing
def test_usgs_parse_real_geojson_shape():
    raw = json.loads(_text("usgs_all_hour.geojson"))
    obs = _agent(USGSFeedAgent).parse(raw)
    assert obs and all({"magnitude", "lat", "lon"} <= set(o) for o in obs)
    # the USGS activation rule (M4.5+) should be exercisable on the fixture
    agent = _agent(USGSFeedAgent)
    breached, _prio, _why = agent.assess(obs)
    if breached:
        assert agent.build_event(obs) is not None


def test_open_meteo_parse_real_json_shape():
    raw = json.loads(_text("open_meteo_forecast.json"))
    obs = _agent(OpenMeteoFeedAgent).parse(raw)
    assert obs, "open-meteo parse produced no observations from the fixture"


def test_firms_parse_real_csv_shape():
    # parse_csv yields raw FIRMS columns; parse() normalises to lat/lon/brightness_k
    rows = FIRMSFeedAgent.parse_csv(_text("firms_viirs.csv"))
    assert rows and all({"latitude", "longitude"} <= set(r) for r in rows)
    detections = _agent(FIRMSFeedAgent).parse(rows)
    assert detections and all({"lat", "lon", "brightness_k"} <= set(d) for d in detections)


# ------------------------------------------------------- fetch via injected transport
def test_usgs_fetch_uses_injected_transport_no_network():
    text = _text("usgs_all_hour.geojson")
    transport = _recording_transport(text)
    result = _agent(USGSFeedAgent).fetch(transport=transport)
    assert transport.calls, "fetch did not use the injected transport (would hit network!)"
    assert "features" in result  # decoded the recorded GeoJSON, not sample()


def test_open_meteo_fetch_uses_injected_transport():
    transport = _recording_transport(_text("open_meteo_forecast.json"))
    result = _agent(OpenMeteoFeedAgent).fetch(transport=transport)
    assert transport.calls
    assert isinstance(result, dict)


# -------------------------------------------------------- graceful degradation
def test_fetch_degrades_to_sample_on_transport_error():
    """A non-2xx / failing transport must fall back to sample(), never raise (Step 10)."""
    agent = _agent(USGSFeedAgent)
    bad = _recording_transport("", status=503)
    result = agent.fetch(transport=bad)
    assert bad.calls
    # degraded result equals the offline sample (same shape the loop already trusts)
    assert result == agent.sample()


def test_default_path_is_offline_sample_parses():
    """The default (non-live) path parses sample() with no transport/network."""
    for cls in (USGSFeedAgent, OpenMeteoFeedAgent):
        agent = _agent(cls)
        assert agent.parse(agent.sample()) is not None
