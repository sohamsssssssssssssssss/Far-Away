"""Durable persistence selection (PRD Step 9).

State survives restarts when ``DM_PERSIST``/``DM_LIVE`` is set: the StatePersistor
is then backed by ``Storage.from_settings(live=True)`` (TimescaleDB / Elasticsearch
/ PostGIS). Default stays in-memory so the suite + unconfigured deploys touch no
database. We assert the SELECTION without contacting a real backend.
"""
from __future__ import annotations

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.persistence.build import _persist_live, build_agents


def test_persist_live_flag(monkeypatch):
    monkeypatch.delenv("DM_PERSIST", raising=False)
    monkeypatch.delenv("DM_LIVE", raising=False)
    assert _persist_live() is False
    monkeypatch.setenv("DM_PERSIST", "1")
    assert _persist_live() is True
    monkeypatch.setenv("DM_PERSIST", "0")
    monkeypatch.setenv("DM_LIVE", "true")  # DM_LIVE also enables it
    assert _persist_live() is True


def test_default_is_offline_in_memory(monkeypatch):
    monkeypatch.delenv("DM_PERSIST", raising=False)
    monkeypatch.delenv("DM_LIVE", raising=False)
    (persistor,) = build_agents(InMemoryBus(), DecisionLogger.null(), Settings())
    assert persistor.storage.all_fallback is True  # no external service contacted


def test_durable_mode_builds_via_from_settings(monkeypatch):
    """With DM_PERSIST set, storage must come from Storage.from_settings(live=True)."""
    monkeypatch.setenv("DM_PERSIST", "1")
    import disastermind.storage as storage_mod

    sentinel = storage_mod.Storage.in_memory()
    seen = {}

    def fake_from_settings(settings=None, *, live=False):
        seen["live"] = live
        return sentinel

    monkeypatch.setattr(storage_mod.Storage, "from_settings", fake_from_settings)
    (persistor,) = build_agents(InMemoryBus(), DecisionLogger.null(), Settings())
    assert seen.get("live") is True      # durable path requested
    assert persistor.storage is sentinel  # and used by the persistor


def test_persistor_writes_through_to_its_store(monkeypatch):
    """Sanity: the persistor actually persists audit records to its store."""
    monkeypatch.delenv("DM_PERSIST", raising=False)
    monkeypatch.delenv("DM_LIVE", raising=False)
    from disastermind.core.contracts import Message, MessageType, Priority, Topic

    bus = InMemoryBus()
    (persistor,) = build_agents(bus, DecisionLogger.null(), Settings())
    bus.publish(
        Message(sender="x", recipient="y", type=MessageType.ALERT, priority=Priority.INFO,
                topic=Topic.RAW_FEED, payload={"k": 1})
    )
    assert persistor.storage.audit.count() >= 1  # audit trail captured
