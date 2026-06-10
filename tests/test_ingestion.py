"""Ingestion feed adapters (PRD Step 2).

Covers the non-alert (informational) tick path that previously crashed with
``AttributeError: MessageType has no attribute INFO`` (base.py used a
non-existent enum member). The frozen MessageType taxonomy has no INFO member,
so a routine non-breach observation must ride as a valid MessageType while
still carrying Priority.INFO.
"""
from __future__ import annotations

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import MessageType, Module, Priority, Topic
from disastermind.tier3.ingestion.hydromet import CWCFeedAgent
from disastermind.tier3.ingestion.seismic import USGSFeedAgent


def _agent(cls):
    return cls(bus=InMemoryBus(), logger=DecisionLogger.null(), settings=Settings())


def test_non_alert_batch_does_not_crash_and_is_not_alert():
    """USGS all-sub-M4.5 batch must emit a non-ALERT RAW_FEED (no AttributeError)."""
    a = _agent(USGSFeedAgent)
    # A batch entirely below the M4.5 activation threshold (informational).
    sub_threshold = {
        "type": "FeatureCollection",
        "features": [
            {
                "id": "q1",
                "properties": {"mag": 2.1, "place": "X", "time": 1, "tsunami": 0},
                "geometry": {"coordinates": [77.0, 31.0, 9.0]},
            },
            {
                "id": "q2",
                "properties": {"mag": 3.0, "place": "Y", "time": 2, "tsunami": 0},
                "geometry": {"coordinates": [78.0, 30.0, 12.0]},
            },
        ],
    }
    observations = a.parse(sub_threshold)
    is_alert, priority, _ = a.assess(observations)
    assert is_alert is False
    assert priority == Priority.INFO

    # Drive the actual tick path that builds the Message (the crash site).
    a.sample = lambda: sub_threshold  # type: ignore[method-assign]
    msgs = a.tick()
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.type is not MessageType.ALERT
    assert isinstance(msg.type, MessageType)  # a real, valid enum member
    assert msg.priority == Priority.INFO
    assert msg.topic == Topic.RAW_FEED
    assert msg.payload["event"] is None  # no breach => no minted event


def test_alert_batch_emits_alert_with_event():
    """The default USGS sample (one M4.9) must emit an ALERT carrying an event."""
    a = _agent(USGSFeedAgent)
    msgs = a.tick()  # default sample has an M4.9 near Guwahati
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.type is MessageType.ALERT
    assert msg.module is Module.EARTHQUAKE
    assert msg.payload["event"] is not None
    assert msg.payload["event"]["kind"] == "earthquake"


def test_hydromet_non_alert_path_is_safe():
    """A below-danger river gauge batch must also survive the non-alert path."""
    a = _agent(CWCFeedAgent)
    safe_stations = [
        {
            "station_id": "S1",
            "river": "R",
            "name": "N",
            "lat": 25.0,
            "lon": 85.0,
            "water_level_m": 1.0,
            "danger_level_m": 50.0,
            "warning_level_m": 48.0,
            "trend": "steady",
        }
    ]
    a.sample = lambda: safe_stations  # type: ignore[method-assign]
    msgs = a.tick()
    assert len(msgs) == 1
    assert msgs[0].type is not MessageType.ALERT
    assert isinstance(msgs[0].type, MessageType)
