"""Shared pytest fixtures for the DisasterMind test-suite.

These fixtures wire the full agent DAG on an in-memory bus so tests can drive a
synthetic disaster through every tier without any network, broker, solver or ML
dependency (PRD Step 10 graceful degradation — stdlib only).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)

# The uniform per-module factory contract (PRD Group A): every module package
# exposes ``build.build_agents(bus, logger, settings) -> list[BaseAgent]``.
MODULE_BUILD_PATHS = [
    "disastermind.tier3.ingestion.build",
    "disastermind.tier3.iot.build",
    "disastermind.tier2.prediction.build",
    "disastermind.tier2.cascade.build",
    "disastermind.tier2.resource.build",
    "disastermind.tier2.routing.build",
    "disastermind.tier2.field.build",
    "disastermind.tier1.commander.build",
    "disastermind.tier3.dispatch.build",
]

# Sample pre-positioned asset inventory (mirrors the resource agent's default).
# GPS beacons with these ids let the field coordinator bind deployment orders to
# real teams so the chain reaches DISPATCH.
SAMPLE_TEAMS = [
    ("BOAT-01", "boat", 20.27, 85.84),
    ("BOAT-02", "boat", 20.35, 85.90),
    ("NDRF-01", "ndrf_team", 20.30, 85.82),
    ("SDRF-01", "sdrf_team", 20.25, 85.88),
    ("MED-01", "medical_unit", 20.29, 85.83),
    ("HELI-01", "helicopter", 20.24, 85.81),
    ("USAR-01", "usar_team", 20.31, 85.86),
    ("FIRE-01", "fire_engine", 20.28, 85.85),
]


@dataclass
class Harness:
    """A fully-wired in-memory DisasterMind runtime for tests."""

    bus: InMemoryBus
    logger: DecisionLogger
    settings: Settings
    agents: list

    def topic_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.bus.history:
            counts[m.topic] = counts.get(m.topic, 0) + 1
        return counts

    def messages_on(self, topic: str) -> list[Message]:
        return [m for m in self.bus.history if m.topic == topic]

    def real_dispatches(self) -> list[Message]:
        """DISPATCH messages that are actual orders (not housekeeping ACKs)."""
        out = []
        for m in self.messages_on(Topic.DISPATCH):
            if m.type is MessageType.ACK:
                continue
            if (m.payload or {}).get("kind") == "dispatch_ack":
                continue
            out.append(m)
        return out

    def seed_field_teams(self, teams=SAMPLE_TEAMS) -> None:
        """Publish a GPS-beacon telemetry frame so the field tier tracks teams."""
        readings = [
            {
                "team_id": tid,
                "asset_type": atype,
                "location": {"lat": lat, "lon": lon},
                "status": "idle",
            }
            for (tid, atype, lat, lon) in teams
        ]
        self.bus.publish(
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

    def run_ingestion_tick(self) -> None:
        """Drive every ingestion feed agent's tick() once (offline samples)."""
        for a in self.agents:
            name = getattr(a, "name", "")
            if name.startswith("ingest."):
                a.run_tick()


def _build_harness() -> Harness:
    bus = InMemoryBus()
    logger = DecisionLogger.null()
    settings = Settings()
    agents: list = []
    # Build reactive subscribers BEFORE the producers so subscriptions exist when
    # ingestion ticks fan out on the synchronous in-memory bus.
    order = [
        "disastermind.tier2.prediction.build",
        "disastermind.tier2.cascade.build",
        "disastermind.tier2.resource.build",
        "disastermind.tier2.routing.build",
        "disastermind.tier2.field.build",
        "disastermind.tier1.commander.build",
        "disastermind.tier3.dispatch.build",
        "disastermind.tier3.ingestion.build",
    ]
    for path in order:
        mod = importlib.import_module(path)
        agents.extend(mod.build_agents(bus, logger, settings))
    return Harness(bus=bus, logger=logger, settings=settings, agents=agents)


@pytest.fixture
def harness() -> Harness:
    """A fully-wired DAG on an in-memory bus, with the audit logger in null mode."""
    return _build_harness()


@pytest.fixture
def disk_harness(tmp_path) -> Harness:
    """Like :func:`harness` but with a real on-disk JSONL audit log.

    Lets tests exercise :meth:`DecisionLogger.verify_chain` against a written
    hash-chain (verify_chain returns True for the null/in-memory logger).
    """
    bus = InMemoryBus()
    logger = DecisionLogger(path=str(tmp_path / "audit.jsonl"))
    settings = Settings()
    agents: list = []
    order = [
        "disastermind.tier2.prediction.build",
        "disastermind.tier2.cascade.build",
        "disastermind.tier2.resource.build",
        "disastermind.tier2.routing.build",
        "disastermind.tier2.field.build",
        "disastermind.tier1.commander.build",
        "disastermind.tier3.dispatch.build",
        "disastermind.tier3.ingestion.build",
    ]
    for path in order:
        mod = importlib.import_module(path)
        agents.extend(mod.build_agents(bus, logger, settings))
    return Harness(bus=bus, logger=logger, settings=settings, agents=agents)
