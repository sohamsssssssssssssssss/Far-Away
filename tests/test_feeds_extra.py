"""Extra Tier 3 feed adapters — FIRMS, Open-Meteo, OpenWeatherMap (PRD Step 2).

Covers the pure ``parse()`` + offline ``sample()`` contract for the three feeds
added to complete the roster, the ALERT vs informational tick paths, the minted
DisasterEvent shape (Module C URBAN_FIRE / Module A FLOOD), and that all three
adapters are wired into :func:`build_agents`. Stdlib-only, no network.
"""
from __future__ import annotations

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import MessageType, Module, Priority, Topic
from disastermind.tier3.ingestion.build import build_agents
from disastermind.tier3.ingestion.openmeteo import OpenMeteoFeedAgent
from disastermind.tier3.ingestion.wildfire import (
    FIRMSFeedAgent,
    OpenWeatherMapFeedAgent,
)


def _agent(cls):
    return cls(bus=InMemoryBus(), logger=DecisionLogger.null(), settings=Settings())


# --------------------------------------------------------------------- FIRMS
def test_firms_parse_normalises_sample():
    a = _agent(FIRMSFeedAgent)
    obs = a.parse(a.sample())
    assert len(obs) == 2
    hot = max(obs, key=lambda o: o["brightness_k"])
    assert hot["brightness_k"] == 364.5
    assert hot["confidence_pct"] == 90.0  # "high" label -> 90%
    assert hot["lat"] == 28.6139 and hot["lon"] == 77.2090
    # Every normalised key the prediction tier relies on is present.
    assert {"id", "lat", "lon", "brightness_k", "confidence_pct", "frp_mw"} <= set(hot)


def test_firms_alert_emits_urban_fire_event():
    a = _agent(FIRMSFeedAgent)
    msgs = a.tick()  # default sample has one 364.5K high-confidence pixel
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.type is MessageType.ALERT
    assert msg.module is Module.FIRE_COLLAPSE
    assert msg.priority == Priority.CRITICAL  # >= 360K critical
    assert msg.topic == Topic.RAW_FEED
    ev = msg.payload["event"]
    assert ev is not None
    assert ev["kind"] == "urban_fire"
    assert ev["incident_id"].startswith("firms:")


def test_firms_low_confidence_or_cool_is_not_alert():
    a = _agent(FIRMSFeedAgent)
    # Hot pixel but low confidence, plus a cool high-confidence pixel: neither breaches.
    a.sample = lambda: [  # type: ignore[method-assign]
        {"id": "x", "latitude": 1.0, "longitude": 2.0, "bright_ti4": 380.0,
         "confidence": "low", "frp": 1.0},
        {"id": "y", "latitude": 3.0, "longitude": 4.0, "bright_ti4": 300.0,
         "confidence": "high", "frp": 1.0},
    ]
    msgs = a.tick()
    assert len(msgs) == 1
    assert msgs[0].type is not MessageType.ALERT
    assert isinstance(msgs[0].type, MessageType)
    assert msgs[0].priority == Priority.INFO
    assert msgs[0].payload["event"] is None


# -------------------------------------------------------------- OpenWeatherMap
def test_owm_parse_normalises_sample():
    a = _agent(OpenWeatherMapFeedAgent)
    obs = a.parse(a.sample())
    assert len(obs) == 2
    windy = max(obs, key=lambda o: o["wind_speed_ms"])
    assert windy["name"] == "Delhi"
    assert windy["wind_speed_ms"] == 16.4
    assert windy["wind_deg"] == 245.0
    assert windy["lat"] == 28.6139


def test_owm_high_wind_flags_high_but_mints_no_event():
    a = _agent(OpenWeatherMapFeedAgent)
    msgs = a.tick()  # sample Delhi wind 16.4 m/s >= 14 m/s threshold
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.type is MessageType.ALERT
    assert msg.module is Module.FIRE_COLLAPSE
    assert msg.priority == Priority.HIGH
    # Wind alone is not a hazard -> no DisasterEvent (PRD Step 2 weather feed).
    assert msg.payload["event"] is None


def test_owm_calm_wind_is_informational():
    a = _agent(OpenWeatherMapFeedAgent)
    a.sample = lambda: [  # type: ignore[method-assign]
        {"id": 1, "name": "Calmville", "coord": {"lat": 0.0, "lon": 0.0},
         "wind": {"speed": 2.0, "deg": 10}, "dt": 1},
    ]
    msgs = a.tick()
    assert len(msgs) == 1
    assert msgs[0].type is not MessageType.ALERT
    assert isinstance(msgs[0].type, MessageType)
    assert msgs[0].priority == Priority.INFO


# ----------------------------------------------------------------- Open-Meteo
def test_open_meteo_parse_flattens_hourly_arrays():
    a = _agent(OpenMeteoFeedAgent)
    obs = a.parse(a.sample())
    assert len(obs) == 3  # one observation per forecast hour
    severe = obs[1]  # the 07:00 hour: 28.6 mm/h, 74.5 km/h, 85%
    assert severe["precip_mm_h"] == 28.6
    assert severe["wind_kmh"] == 74.5
    assert severe["storm_pct"] == 85.0
    # AOI coords are attached to every flattened hour.
    assert all(o["lat"] == 19.31 and o["lon"] == 86.61 for o in obs)


def test_open_meteo_severe_hour_emits_critical_flood_event():
    a = _agent(OpenMeteoFeedAgent)
    msgs = a.tick()  # 07:00 has both heavy rain AND gale wind -> critical
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.type is MessageType.ALERT
    assert msg.module is Module.CYCLONE_FLOOD
    assert msg.priority == Priority.CRITICAL
    assert msg.topic == Topic.RAW_FEED
    ev = msg.payload["event"]
    assert ev is not None
    assert ev["kind"] == "flood"
    assert ev["source"] == "Open-Meteo"


def test_open_meteo_calm_forecast_is_informational():
    a = _agent(OpenMeteoFeedAgent)
    a.sample = lambda: {  # type: ignore[method-assign]
        "latitude": 12.0,
        "longitude": 77.0,
        "hourly": {
            "time": ["2026-06-08T06:00", "2026-06-08T07:00"],
            "precipitation": [0.2, 1.1],
            "wind_speed_10m": [10.0, 12.0],
            "wind_gusts_10m": [18.0, 20.0],
            "thunderstorm_probability": [5.0, 10.0],
        },
    }
    msgs = a.tick()
    assert len(msgs) == 1
    assert msgs[0].type is not MessageType.ALERT
    assert isinstance(msgs[0].type, MessageType)
    assert msgs[0].priority == Priority.INFO
    assert msgs[0].payload["event"] is None


# ------------------------------------------------------------------- wiring
def test_new_feeds_registered_in_build_agents():
    agents = build_agents(InMemoryBus(), DecisionLogger.null(), Settings())
    feed_names = {getattr(a, "feed_name", None) for a in agents}
    # The three new feeds plus the pre-existing roster.
    assert {"firms", "openweathermap", "open_meteo"} <= feed_names
    assert {"usgs", "ncs", "cwc_wris", "imd", "bhuvan"} <= feed_names
    # All are pure edge producers (no decision authority, no subscriptions).
    new = [a for a in agents if getattr(a, "feed_name", None) in
           {"firms", "openweathermap", "open_meteo"}]
    assert len(new) == 3
    for a in new:
        assert a.decision_authority is False
