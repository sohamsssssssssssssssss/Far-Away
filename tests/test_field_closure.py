"""Tests for the field-loop closure helper (PRD Step 6/8).

Exercises :func:`disastermind.fieldapp.attach_field_clients`, which registers a
:class:`MockFieldClient` per field team onto a loop's bus so the dispatch ->
ACK -> GPS -> field-coordination loop closes:

    Commander DISPATCH -> matching client auto-ACKs, emitting
      (i)  a Topic.IOT_TELEMETRY ``gps_beacon`` advancing idle->enroute->onsite
           (the exact shape the Tier-2 FieldCoordinationAgent consumes), and
      (ii) an OrderAck on FIELDAPP_ACK back to the dispatcher.

The headline test attaches clients to a driven earthquake scenario, runs cycles,
and asserts BOTH that OrderAcks appear on FIELDAPP_ACK and that a team's status
advanced (enroute/onsite) in the telemetry the field tier actually observed.

Stdlib-only; no network, no broker, no optional libs.
"""
from __future__ import annotations

from disastermind.core.bus import InMemoryBus
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.fieldapp import MockFieldClient, attach_field_clients
from disastermind.fieldapp.contracts import FIELDAPP_ACK
from disastermind.models.domain import AssetType
from disastermind.models.geo import LatLon
from disastermind.scenarios.base import DEFAULT_TEAMS, build_loop, seed_field_teams
from disastermind.scenarios.earthquake import simulate_earthquake


def _dispatch(team_id: str, site: str = "zone-7", order_id: str = "o-1", **extra) -> Message:
    """A Tier-1-style DISPATCH carrying a single ``payload['order']`` for a team."""
    order = {
        "order_id": order_id,
        "team_id": team_id,
        "site": site,
        "priority": 1,
        "reason": "rescue",
        **extra,
    }
    return Message(
        sender="commander",
        recipient="dispatch",
        type=MessageType.INSTRUCTION,
        priority=Priority.CRITICAL,
        topic=Topic.DISPATCH,
        incident_id="inc-1",
        module=Module.EARTHQUAKE,
        payload={"order": order, "body": "go"},
    )


def _beacons(history) -> list[Message]:
    return [
        m
        for m in history
        if m.topic == Topic.IOT_TELEMETRY
        and (m.payload or {}).get("kind") == "gps_beacon"
    ]


def _acks(history) -> list[Message]:
    return [m for m in history if m.topic == FIELDAPP_ACK]


# ----------------------------------------------------------- attachment basics
def test_attach_with_explicit_team_ids_strings():
    """Explicit team-id strings attach one bound, live client each."""
    bus = InMemoryBus()
    clients = attach_field_clients(bus, ["NDRF-01", "BOAT-02"])
    assert [c.team_id for c in clients] == ["NDRF-01", "BOAT-02"]
    assert all(isinstance(c, MockFieldClient) for c in clients)
    # Live on the bus: a dispatch reaches the matching client immediately.
    bus.publish(_dispatch("NDRF-01"))
    assert clients[0].status == "enroute"
    assert _acks(bus.history), "attached client did not ACK a dispatch"


def test_attach_with_explicit_tuple_specs_sets_asset_and_location():
    bus = InMemoryBus()
    clients = attach_field_clients(bus, [("MED-01", "medical_unit", 20.29, 85.83)])
    c = clients[0]
    assert c.asset_type is AssetType.MEDICAL_UNIT
    assert c.location == LatLon(20.29, 85.83)


def test_attach_accepts_loop_or_bare_bus():
    """attach_field_clients takes a CoordinationLoop (uses .bus) or a bare bus."""
    loop = build_loop()
    clients = attach_field_clients(loop, ["NDRF-01"])
    assert len(clients) == 1
    # The client is wired to the loop's bus, not some other bus.
    loop.bus.publish(_dispatch("NDRF-01"))
    assert clients[0].status == "enroute"


def test_attach_with_no_teams_resolves_default_roster():
    """team_ids=None on a fresh loop falls back to the scenario roster."""
    loop = build_loop()
    clients = attach_field_clients(loop)
    assert {c.team_id for c in clients} == {t[0] for t in DEFAULT_TEAMS}


def test_attach_discovers_teams_from_seeded_coordinator():
    """team_ids=None prefers the field coordinator's already-tracked roster."""
    loop = build_loop()
    seed_field_teams(loop.bus, teams=[("NDRF-07", "ndrf_team", 20.3, 85.8)])
    coord = next(a for a in loop.agents if getattr(a, "name", "") == "field_coordinator")
    assert "NDRF-07" in coord.teams  # coordinator saw the seed beacon

    clients = attach_field_clients(loop)
    assert [c.team_id for c in clients] == ["NDRF-07"]
    assert clients[0].asset_type is AssetType.NDRF_TEAM


def test_attach_empty_when_no_teams_and_no_roster():
    """A bare bus with explicit empty team_ids is a pure no-op."""
    bus = InMemoryBus()
    before = len(bus.history)
    assert attach_field_clients(bus, []) == []
    assert len(bus.history) == before


# ------------------------------------------------- closed loop on a real scenario
def test_closed_loop_on_driven_earthquake_scenario():
    """HEADLINE: dispatch -> ACK -> GPS -> field-coordination closes end-to-end.

    Attach clients to a driven earthquake loop, run cycles, then assert:
      * OrderAck messages appear on FIELDAPP_ACK (the dispatcher's receipt), and
      * a team's status advanced to enroute/onsite in the telemetry the Tier-2
        field coordinator actually consumed (proving the loop closed, not just
        that a beacon was emitted into the void).
    """
    loop = build_loop()
    # Close the loop BEFORE driving: clients must be subscribed when the
    # Commander starts dispatching so they observe orders synchronously.
    clients = attach_field_clients(loop)
    assert clients, "no field clients attached"

    simulate_earthquake(loop, drive_cycles=3)

    # (ii) OrderAcks landed on FIELDAPP_ACK, addressed back to the dispatcher.
    acks = _acks(loop.bus.history)
    assert acks, "no OrderAck appeared on FIELDAPP_ACK — loop did not close"
    assert acks[-1].type is MessageType.ACK
    assert acks[-1].payload["kind"] == "order_ack"
    assert acks[-1].payload["status"] == "accepted"
    acked_teams = {m.payload["team_id"] for m in acks}

    # At least one client advanced past idle in response to a real dispatch.
    advanced_clients = {c.team_id for c in clients if c.status in ("enroute", "onsite")}
    assert advanced_clients, "no attached client advanced off idle"
    assert advanced_clients & acked_teams, "advanced teams did not ACK"

    # (i) The Tier-2 field coordinator SAW the advance: a team it tracks reached
    # enroute/onsite, fed purely by the clients' gps_beacon telemetry.
    coord = next(a for a in loop.agents if getattr(a, "name", "") == "field_coordinator")
    advanced_in_coord = {
        tid for tid, t in coord.teams.items() if t.status in ("enroute", "onsite")
    }
    assert advanced_in_coord, "field coordinator never saw a team advance off idle"
    assert advanced_in_coord & advanced_clients, (
        "the team the coordinator saw advance is not one a client drove"
    )

    # The advancing beacons are the client's, carrying the consumed reading shape.
    client_names = {c.name for c in clients}
    advancing_beacons = [
        b
        for b in _beacons(loop.bus.history)
        if b.sender in client_names
        and any(
            isinstance(r, dict) and r.get("status") in ("enroute", "onsite")
            for r in (b.payload or {}).get("readings", [])
        )
    ]
    assert advancing_beacons, "no client gps_beacon advanced a team off idle"
    reading = advancing_beacons[-1].payload["readings"][0]
    assert {"team_id", "asset_type", "location", "status"} <= set(reading)


def test_unrelated_dispatch_leaves_attached_clients_idle():
    """A dispatch for a non-attached team must not advance any client."""
    bus = InMemoryBus()
    clients = attach_field_clients(bus, ["NDRF-01", "BOAT-02"])
    bus.publish(_dispatch("HELI-99"))
    assert all(c.status == "idle" for c in clients)
    assert not _acks(bus.history)


def test_attach_does_not_disturb_existing_fieldapp_behaviour():
    """Sanity: attaching is purely additive — no clients means no bus traffic."""
    bus = InMemoryBus()
    before = len(bus.history)
    attach_field_clients(bus, [])
    # No telemetry / acks emitted merely by attaching.
    assert not _beacons(bus.history)
    assert not _acks(bus.history)
    assert len(bus.history) == before
