"""Tests for the OPTIONAL field-app package (PRD Step 8 / Step 6).

Verifies the device-facing contracts and the :class:`MockFieldClient` loop:
  * a dispatched order to a client's team is auto-ACKed,
  * the client emits a ``kind="gps_beacon"`` IOT_TELEMETRY frame whose reading
    shape (``{team_id, asset_type, location, status}``) the Tier-2 field
    coordinator consumes, advancing idle -> enroute -> onsite,
  * over-capacity reporting drives the coordinator's autonomous resource request,
  * the optional ``build.build_agents`` factory stays out of the autonomous DAG
    (returns ``[]`` with default settings).

Stdlib-only; no network, no broker, no optional libs.
"""
from __future__ import annotations

from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.fieldapp import (
    DeploymentOrderMsg,
    MockFieldClient,
    OrderAck,
    SiteOverCapacityReport,
    TeamStatusUpdate,
)
from disastermind.fieldapp.build import build_agents, build_clients
from disastermind.fieldapp.contracts import FIELDAPP_ACK
from disastermind.models.domain import AssetType
from disastermind.models.geo import LatLon


def _dispatch(team_id: str, site: str = "zone-7", order_id: str = "o-1", **extra) -> Message:
    """A Tier-1-style DISPATCH carrying a single ``payload['order']`` for a team."""
    order = {
        "order_id": order_id,
        "team_id": team_id,
        "site": site,
        "priority": 1,
        "reason": "rescue",
        "channel": "terrestrial",
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
        payload={"channel": "terrestrial", "order": order, "body": "go"},
    )


# --------------------------------------------------------------------- contracts
def test_team_status_update_reading_shape():
    """TeamStatusUpdate renders exactly the beacon shape the field tier consumes."""
    upd = TeamStatusUpdate(
        team_id="NDRF-01",
        asset_type=AssetType.NDRF_TEAM,
        location=LatLon(20.3, 85.8),
        status="enroute",
    )
    r = upd.to_reading()
    assert r["team_id"] == "NDRF-01"
    assert r["asset_type"] == "ndrf_team"
    assert r["location"] == {"lat": 20.3, "lon": 85.8}
    assert r["status"] == "enroute"


def test_deployment_order_from_dispatch_payload():
    """DeploymentOrderMsg projects a dispatch order dict onto the device contract."""
    dom = DeploymentOrderMsg.from_payload(
        {"order_id": "o-9", "team_id": "BOAT-01", "target_cell": "cell-3", "priority": 2},
        incident_id="inc-x",
    )
    assert dom.order_id == "o-9"
    assert dom.team_id == "BOAT-01"
    assert dom.site == "cell-3"
    assert dom.priority == 2
    assert dom.incident_id == "inc-x"


def test_over_capacity_report_reading_flags_site():
    """SiteOverCapacityReport renders a beacon the coordinator treats as a request."""
    rep = SiteOverCapacityReport(team_id="MED-01", site="hosp-2", shortfall=3)
    reading = rep.to_reading(LatLon(20.29, 85.83), AssetType.MEDICAL_UNIT)
    assert reading["site_over_capacity"] is True
    assert reading["shortfall"] == 3
    assert reading["team_id"] == "MED-01"


def test_order_ack_is_dataclass():
    ack = OrderAck(order_id="o-1", team_id="NDRF-01", status="accepted")
    assert ack.status == "accepted"
    assert ack.ts  # auto timestamp


# ------------------------------------------------------------------- mock client
def test_client_acks_dispatch_and_emits_gps_update():
    """REQUIRED: client receives a dispatched order, ACKs it, emits a GPS update.

    The emitted GPS update must have the shape the field tier consumes:
    ``kind="gps_beacon"``, readings ``[{team_id, asset_type, location, status}]``.
    """
    bus = InMemoryBus()
    client = MockFieldClient(
        team_id="NDRF-01",
        bus=bus,
        asset_type=AssetType.NDRF_TEAM,
        location=LatLon(20.30, 85.82),
    )

    bus.publish(_dispatch("NDRF-01"))

    beacons = [
        m
        for m in bus.history
        if m.topic == Topic.IOT_TELEMETRY
        and (m.payload or {}).get("kind") == "gps_beacon"
    ]
    assert beacons, "client emitted no gps_beacon on dispatch"
    reading = beacons[-1].payload["readings"][0]
    assert set(("team_id", "asset_type", "location", "status")) <= set(reading)
    assert reading["team_id"] == "NDRF-01"
    assert reading["asset_type"] == "ndrf_team"
    assert reading["status"] == "enroute"  # idle -> enroute on first order
    assert reading["location"] == {"lat": 20.30, "lon": 85.82}

    acks = [m for m in bus.history if m.topic == FIELDAPP_ACK]
    assert acks, "client emitted no order acknowledgement"
    assert acks[-1].type is MessageType.ACK
    assert acks[-1].payload["team_id"] == "NDRF-01"
    assert acks[-1].payload["status"] == "accepted"

    assert client.status == "enroute"
    assert client.assignment == "zone-7"


def test_client_ignores_orders_for_other_teams():
    bus = InMemoryBus()
    client = MockFieldClient(team_id="NDRF-01", bus=bus)
    bus.publish(_dispatch("BOAT-99"))
    assert not [m for m in bus.history if m.topic in (Topic.IOT_TELEMETRY, FIELDAPP_ACK)]
    assert client.status == "idle"


def test_client_advances_idle_enroute_onsite_and_is_idempotent():
    """Re-issuing advances enroute -> onsite; duplicate order id is a no-op."""
    bus = InMemoryBus()
    client = MockFieldClient(team_id="NDRF-01", bus=bus)

    bus.publish(_dispatch("NDRF-01", order_id="a", waypoints=[{"lat": 1.0, "lon": 2.0}]))
    assert client.status == "enroute"

    bus.publish(_dispatch("NDRF-01", order_id="b", waypoints=[{"lat": 1.0, "lon": 2.0}]))
    assert client.status == "onsite"
    assert client.location == LatLon(1.0, 2.0)  # snapped to last waypoint on arrival

    before = len(bus.history)
    bus.publish(_dispatch("NDRF-01", order_id="b"))  # duplicate -> ignored
    assert len(bus.history) == before + 1  # only the dispatch itself landed


def test_client_ignores_dispatch_acks():
    """The dispatch router's own housekeeping ACK must not trigger the client."""
    bus = InMemoryBus()
    client = MockFieldClient(team_id="NDRF-01", bus=bus)
    bus.publish(
        Message(
            sender="dispatch.router",
            recipient="commander",
            type=MessageType.ACK,
            priority=Priority.LOW,
            topic=Topic.DISPATCH,
            payload={"kind": "dispatch_ack", "delivered": 1},
        )
    )
    assert client.status == "idle"
    assert not [m for m in bus.history if m.topic in (Topic.IOT_TELEMETRY, FIELDAPP_ACK)]


# --------------------------------------------------- closed loop with field tier
def test_field_tier_consumes_client_gps_update():
    """The Tier-2 field coordinator ingests the client's beacon into team state.

    This proves the emitted update's shape is exactly what the field tier reads:
    after the client beacons, the coordinator tracks the team and its status.
    """
    from disastermind.tier2.field.agent import FieldCoordinationAgent

    bus = InMemoryBus()
    coord = FieldCoordinationAgent(bus=bus)
    client = MockFieldClient(
        team_id="NDRF-01", bus=bus, asset_type=AssetType.NDRF_TEAM,
        location=LatLon(20.30, 85.82),
    )

    # Client beacons its idle position; coordinator should now track the team.
    client.beacon()
    assert "NDRF-01" in coord.teams
    assert coord.teams["NDRF-01"].asset_type is AssetType.NDRF_TEAM

    # A dispatch reaches the client -> it auto-beacons enroute; coordinator updates.
    bus.publish(_dispatch("NDRF-01"))
    assert coord.teams["NDRF-01"].status == "enroute"


def test_over_capacity_report_triggers_resource_request():
    """A client over-capacity beacon makes the coordinator ask for reinforcement."""
    from disastermind.tier2.field.agent import FieldCoordinationAgent

    bus = InMemoryBus()
    coord = FieldCoordinationAgent(bus=bus)
    client = MockFieldClient(
        team_id="MED-01", bus=bus, asset_type=AssetType.MEDICAL_UNIT,
        location=LatLon(20.29, 85.83),
    )
    client.beacon()
    client.report_over_capacity(site="hosp-2", shortfall=2, incident_id="inc-1")

    requests = [
        m
        for m in bus.history
        if m.topic == Topic.RESOURCE_PLAN
        and (m.payload or {}).get("kind") == "resource_request"
    ]
    assert requests, "coordinator did not request extra resources for over-capacity site"
    assert requests[-1].payload["from_team"] == "MED-01"
    assert requests[-1].payload["shortfall"] == 2


# ----------------------------------------------------------------------- factory
def test_build_agents_is_empty_by_default():
    """The field app must NOT auto-wire into the autonomous DAG (PRD Step 8)."""
    bus = InMemoryBus()
    assert build_agents(bus, None, Settings()) == []


def test_build_clients_creates_bound_clients():
    bus = InMemoryBus()
    clients = build_clients(
        bus, [("NDRF-01", "ndrf_team", 20.3, 85.8), "BOAT-02"]
    )
    assert [c.team_id for c in clients] == ["NDRF-01", "BOAT-02"]
    assert clients[0].asset_type is AssetType.NDRF_TEAM
    assert clients[0].location == LatLon(20.3, 85.8)
    assert clients[1].asset_type is AssetType.NDRF_TEAM  # default


def test_build_agents_from_settings_attribute():
    """Opt-in via a settings attribute creates demo clients."""
    bus = InMemoryBus()
    settings = Settings()
    settings.fieldapp_teams = [("HELI-01", "helicopter", 20.24, 85.81)]
    agents = build_agents(bus, None, settings)
    assert len(agents) == 1
    assert agents[0].team_id == "HELI-01"
    assert agents[0].asset_type is AssetType.HELICOPTER
