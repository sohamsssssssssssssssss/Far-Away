"""Shared scenario plumbing: build the DAG, seed teams, inject signals, drive.

This mirrors the verified test harness (``tests/conftest.py``) but lives inside
the ``scenarios`` package so the CLI can run a self-contained, offline,
deterministic disaster simulation for any module without importing test code
(PRD Group A, Step 10).

Wiring uses the proven *subscriber-before-producer* order from
:func:`disastermind.orchestration.build.build_system` so subscriptions exist
before any synchronous in-memory fan-out. We deliberately wire the loop
ourselves (rather than calling ``build_system``) so a scenario can seed field
teams and inject a synthetic hazard signal *before* driving the chain — exactly
how the e2e test drives the pipeline.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

from ..audit.decision_log import DecisionLogger
from ..core.bus import InMemoryBus, MessageBus
from ..core.config import Settings
from ..core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from ..orchestration.loop import CoordinationLoop

# Subscriber-before-producer load order (mirrors build_system / conftest). We
# include the IoT gateways and ingestion feeds last; scenarios inject their own
# synthetic signals, so ingestion ticks are optional.
_SUBSCRIBER_FIRST_PATHS = [
    "disastermind.tier2.prediction.build",
    "disastermind.tier2.cascade.build",
    "disastermind.tier2.resource.build",
    "disastermind.tier2.routing.build",
    "disastermind.tier2.field.build",
    "disastermind.tier1.commander.build",
    "disastermind.tier3.dispatch.build",
    "disastermind.tier3.iot.build",
    "disastermind.tier3.ingestion.build",
]

# A realistic pre-positioned, mixed-asset roster placed near the synthetic
# incident epicentre (Bhubaneswar/Cuttack belt). GPS beacons with these ids let
# the field coordinator bind deployment orders to real teams so the chain
# reaches DISPATCH (mirrors ``tests/conftest.py::SAMPLE_TEAMS``).
DEFAULT_TEAMS: list[tuple[str, str, float, float]] = [
    ("BOAT-01", "boat", 20.27, 85.84),
    ("BOAT-02", "boat", 20.35, 85.90),
    ("NDRF-01", "ndrf_team", 20.30, 85.82),
    ("NDRF-02", "ndrf_team", 20.33, 85.88),
    ("SDRF-01", "sdrf_team", 20.25, 85.88),
    ("MED-01", "medical_unit", 20.29, 85.83),
    ("MED-02", "medical_unit", 20.31, 85.86),
    ("HELI-01", "helicopter", 20.24, 85.81),
    ("USAR-01", "usar_team", 20.31, 85.86),
    ("USAR-02", "usar_team", 20.28, 85.80),
    ("FIRE-01", "fire_engine", 20.28, 85.85),
    ("FIRE-02", "fire_engine", 20.30, 85.87),
]


@dataclass
class ScenarioResult:
    """Outcome of a driven scenario (CLI/test friendly)."""

    module: Module
    label: str
    loop: CoordinationLoop
    topic_counts: dict[str, int] = field(default_factory=dict)
    dispatches: list[Message] = field(default_factory=list)
    escalations: list[Message] = field(default_factory=list)

    @property
    def reached_dispatch(self) -> bool:
        return bool(self.dispatches)

    @property
    def reached_escalation(self) -> bool:
        return bool(self.escalations)

    @property
    def succeeded(self) -> bool:
        """A scenario "lands" if it reached a DISPATCH or an ESCALATION."""
        return self.reached_dispatch or self.reached_escalation


# --------------------------------------------------------------------------- DAG
def build_loop(
    bus: MessageBus | None = None,
    logger: DecisionLogger | None = None,
    settings: Settings | None = None,
) -> CoordinationLoop:
    """Wire the full agent DAG on one bus (subscriber-before-producer order).

    Defensive like :func:`build_system`: a module that fails to import/construct
    is skipped so the rest of the chain still runs (PRD Step 10).
    """
    bus = bus or InMemoryBus()
    logger = logger or DecisionLogger.null()
    settings = settings or Settings()
    agents: list = []
    degraded: list[str] = []
    for path in _SUBSCRIBER_FIRST_PATHS:
        try:
            mod = importlib.import_module(path)
            agents.extend(mod.build_agents(bus, logger, settings))
        except Exception:  # pragma: no cover - graceful degradation
            degraded.append(path)
    loop = CoordinationLoop(bus=bus, logger=logger, settings=settings, agents=agents)
    loop.degraded_modules = degraded
    return loop


# -------------------------------------------------------------------- seeding
def seed_field_teams(
    bus: MessageBus,
    teams: list[tuple[str, str, float, float]] | None = None,
) -> None:
    """Publish a GPS-beacon telemetry frame so the field tier tracks teams.

    Identical shape to ``tests/conftest.py::Harness.seed_field_teams`` — the
    field coordinator binds deployment orders to these beacons, which is what
    lets the chain reach a real DISPATCH (PRD Step 2 / Step 6).
    """
    teams = teams or DEFAULT_TEAMS
    readings = [
        {
            "team_id": tid,
            "asset_type": atype,
            "location": {"lat": lat, "lon": lon},
            "status": "idle",
        }
        for (tid, atype, lat, lon) in teams
    ]
    bus.publish(
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


# ------------------------------------------------------------------ injection
def inject_raw_event(
    bus: MessageBus,
    *,
    kind: str,
    module: Module,
    incident_id: str,
    lat: float,
    lon: float,
    severity: float,
    meta: dict[str, Any] | None = None,
    observations: list[dict[str, Any]] | None = None,
    priority: Priority = Priority.CRITICAL,
    reasoning: list[str] | None = None,
) -> Message:
    """Inject a synthetic RAW_FEED ALERT carrying a DisasterEvent dict.

    This is the same envelope a Tier 3 feed adapter emits from ``tick()`` (see
    :mod:`disastermind.tier3.ingestion.base`): ``payload["event"]`` is a
    JSON-able :class:`~disastermind.models.domain.DisasterEvent`. The prediction
    tier (Tier 2) keys off ``event["kind"]`` and ``message.module`` to forecast,
    so injecting one event deterministically drives a single module's pipeline.
    """
    event = {
        "incident_id": incident_id,
        "kind": kind,
        "epicentre": {"lat": float(lat), "lon": float(lon)},
        "severity": float(severity),
        "detected_at": "2026-06-08T00:00:00+00:00",
        "source": "scenario_simulator",
        "meta": dict(meta or {}),
    }
    msg = Message(
        sender="ingest.scenario",
        recipient="tier2.prediction",
        type=MessageType.ALERT,
        priority=priority,
        payload={
            "kind": "scenario_signal",
            "event": event,
            "observations": list(observations or []),
        },
        reasoning=reasoning or [f"synthetic {kind} signal injected by scenario simulator"],
        topic=Topic.RAW_FEED,
        module=module,
        incident_id=incident_id,
    )
    bus.publish(msg)
    return msg


def inject_rescue_prediction(
    bus: MessageBus,
    *,
    module: Module,
    incident_id: str,
    risk_cells: list[dict[str, Any]],
    sender: str = "tier2.prediction.scenario",
    priority: Priority = Priority.CRITICAL,
    reasoning: list[str] | None = None,
) -> Message:
    """Inject a synthetic PREDICTION (``kind="risk"``) with rescue-priority cells.

    The Tier 2 resource allocator builds deployment demand from prediction
    ``risk_cells`` (population_at_risk per 100 m cell). Module C's fire-spread
    forecaster emits ``fire_fronts`` rather than rescue-zone ``risk_cells``, so a
    fire scenario surfaces its rescue zones here to feed the
    resource -> routing -> field -> dispatch chain (PRD Step 4-7). Modules A/B
    already emit ``risk_cells`` from prediction, so they don't need this.
    """
    msg = Message(
        sender=sender,
        recipient="tier2.cascade",
        type=MessageType.ALERT,
        priority=priority,
        payload={
            "kind": "risk",
            "incident_id": incident_id,
            "module": module.value,
            "risk_cells": list(risk_cells),
            "buildings": [],
            "fire_fronts": [],
        },
        reasoning=reasoning or ["synthetic rescue-priority risk cells (scenario simulator)"],
        topic=Topic.PREDICTION,
        incident_id=incident_id,
        module=module,
    )
    bus.publish(msg)
    return msg


def inject_escalation_order(
    bus: MessageBus,
    *,
    module: Module,
    incident_id: str,
    trigger: EscalationTrigger,
    team_id: str,
    site: str,
    reason: str,
    summary: str,
    scale: int = 1,
    priority: Priority = Priority.CRITICAL,
) -> Message:
    """Inject a FIELD_ORDER carrying an escalation trigger (PRD Step 7).

    The Commander reviews it against the autonomy matrix and — for a trigger
    outside autonomous authority — publishes a :data:`Topic.ESCALATION` to the
    human dashboard instead of dispatching it autonomously.
    """
    msg = Message(
        sender="field_coordinator",
        recipient="commander",
        type=MessageType.INSTRUCTION,
        priority=priority,
        topic=Topic.FIELD_ORDER,
        incident_id=incident_id,
        module=module,
        escalation_trigger=trigger,
        payload={
            "kind": "field_order",
            "incident_id": incident_id,
            "orders": [
                {
                    "team_id": team_id,
                    "site": site,
                    "priority": 1,
                    "reason": reason,
                }
            ],
            "escalation": {
                "trigger": trigger.value,
                "summary": summary,
                "scale": scale,
            },
        },
    )
    bus.publish(msg)
    return msg


# ------------------------------------------------------------------- summary
def topic_counts(loop: CoordinationLoop) -> dict[str, int]:
    """Count messages published per topic across the run (deterministic order)."""
    counts: dict[str, int] = {}
    for m in loop.bus.history:
        counts[m.topic] = counts.get(m.topic, 0) + 1
    return dict(sorted(counts.items()))


def real_dispatches(loop: CoordinationLoop) -> list[Message]:
    """DISPATCH messages that are real orders (not delivery ACKs)."""
    out: list[Message] = []
    for m in loop.bus.history:
        if m.topic != Topic.DISPATCH:
            continue
        if m.type is MessageType.ACK:
            continue
        if (m.payload or {}).get("kind") == "dispatch_ack":
            continue
        out.append(m)
    return out


def escalations(loop: CoordinationLoop) -> list[Message]:
    """ESCALATION messages addressed to the human dashboard (PRD Step 7)."""
    return [
        m
        for m in loop.bus.history
        if m.topic == Topic.ESCALATION and m.type is MessageType.ESCALATION
    ]


def summarise_loop(loop: CoordinationLoop, module: Module, label: str) -> ScenarioResult:
    """Bundle a driven loop into a :class:`ScenarioResult` for CLI/tests."""
    return ScenarioResult(
        module=module,
        label=label,
        loop=loop,
        topic_counts=topic_counts(loop),
        dispatches=real_dispatches(loop),
        escalations=escalations(loop),
    )


# ----------------------------------------------------------------- dispatch map
#: name -> generator. Populated by the per-module modules at import time to
#: avoid a circular import (each generator imports helpers from this module).
SCENARIO_GENERATORS: dict[str, Callable[..., CoordinationLoop]] = {}


def run_scenario(module_key: str, **kwargs) -> ScenarioResult:
    """Run the scenario for a module key (``A`` / ``B`` / ``C``) and summarise.

    Raises :class:`KeyError` for an unknown key so the CLI can report it.
    """
    key = (module_key or "").strip().upper()
    gen = SCENARIO_GENERATORS.get(key)
    if gen is None:
        raise KeyError(key)
    loop = gen(**kwargs)
    module, label = _MODULE_LABELS[key]
    return summarise_loop(loop, module, label)


_MODULE_LABELS: dict[str, tuple[Module, str]] = {
    "A": (Module.CYCLONE_FLOOD, "Cyclone / Flood (Module A)"),
    "B": (Module.EARTHQUAKE, "Earthquake (Module B)"),
    "C": (Module.FIRE_COLLAPSE, "Urban Fire / Collapse (Module C)"),
}
