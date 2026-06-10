"""Tests for the state-persistence integration (PRD Step 9).

Drives a full, offline, stdlib-only DisasterMind run with a
:class:`~disastermind.persistence.persistor.StatePersistor` wired onto the same
bus *before* the producers, then asserts the live message stream landed in the
durable storage repos:

  * every message was indexed in the Elasticsearch audit repo (count > 0),
  * IoT telemetry readings became TelemetryPoint rows (query_range / latest),
  * a deploying asset from the RESOURCE_PLAN is retrievable from the PostGIS repo.

No network, broker, solver or ML dependency (PRD Step 10 graceful degradation).
"""
from __future__ import annotations

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from disastermind.persistence import StatePersistor, all_topics
from disastermind.persistence.build import build_agents
from disastermind.scenarios.base import (
    build_loop,
    inject_raw_event,
    seed_field_teams,
)
from disastermind.storage import Storage, TelemetryPoint


# --------------------------------------------------------------------------- helpers
def _drive_with_persistor() -> StatePersistor:
    """Wire a persistor onto a fresh bus, build + drive a cyclone/flood run.

    The persistor is constructed *before* :func:`build_loop` so its all-topic
    subscriptions exist before any synchronous in-memory fan-out (it must see the
    producers' output), mirroring the subscriber-before-producer discipline.
    """
    bus = InMemoryBus()
    logger = DecisionLogger.null()
    settings = Settings()
    # Subscribe the persistor first.
    persistor = build_agents(bus, logger, settings)[0]
    # Then wire the rest of the DAG on the same bus and drive a real scenario.
    loop = build_loop(bus, logger, settings)
    seed_field_teams(bus)
    inject_raw_event(
        bus,
        kind="flood",
        module=Module.CYCLONE_FLOOD,
        incident_id="test:persistence-cyclone",
        lat=20.30,
        lon=85.84,
        severity=3.0,
        meta={"river_level_m": 6.5, "warning_colour": "red"},
        observations=[{"population": 1500}],
    )
    loop.run(max_cycles=2, clock=lambda: 0.0, sleep=lambda _s: None)
    return persistor


# --------------------------------------------------------------------------- tests
def test_persistor_is_zero_authority_edge_observer() -> None:
    persistor = StatePersistor(bus=InMemoryBus(), logger=DecisionLogger.null())
    assert persistor.tier is Tier.EDGE
    assert persistor.decision_authority is False
    # Defaults to an offline in-memory Storage (no network).
    assert isinstance(persistor.storage, Storage)
    assert persistor.storage.all_fallback is True


def test_all_topics_introspects_every_public_topic() -> None:
    topics = all_topics()
    assert Topic.IOT_TELEMETRY in topics
    assert Topic.RESOURCE_PLAN in topics
    assert Topic.DISPATCH in topics
    assert all(isinstance(t, str) for t in topics)


def test_persistor_emits_nothing() -> None:
    """handle() never returns an outbound message (read-only, PRD Step 10)."""
    bus = InMemoryBus()
    persistor = StatePersistor(bus=bus, logger=DecisionLogger.null())
    msg = Message(
        sender="x",
        recipient="y",
        type=MessageType.ALERT,
        priority=Priority.INFO,
        topic=Topic.PREDICTION,
    )
    assert persistor.handle(msg) == []


def test_audit_records_persisted_for_every_message() -> None:
    persistor = _drive_with_persistor()
    audit = persistor.storage.audit
    # Every bus message was indexed (count > 0) and matches our write counter.
    assert audit.count() > 0
    assert persistor.audit_writes == audit.count()
    # The injected RAW_FEED event is searchable in the durable index.
    hits = audit.search(text="persistence-cyclone")
    assert hits, "injected incident should be searchable in the audit index"


def test_telemetry_rows_landed_in_timeseries_repo() -> None:
    persistor = _drive_with_persistor()
    telem = persistor.storage.telemetry
    assert telem.count() > 0
    assert persistor.telemetry_writes == telem.count()
    # The IoT gateways publish numeric sensor metrics; at least one landed.
    rows = telem.query_range()
    assert rows, "telemetry hypertable should contain readings"
    metrics = {p.metric for p in rows}
    assert metrics, "telemetry points carry a metric label"
    # latest() resolves a most-recent reading for a real sensor.
    some = rows[0]
    assert telem.latest(some.sensor_id) is not None


def test_water_level_telemetry_metric_is_mapped() -> None:
    """A waterlogging IoT frame maps each reading's water_level_m to a point."""
    bus = InMemoryBus()
    persistor = StatePersistor(bus=bus, logger=DecisionLogger.null())
    bus.publish(
        Message(
            sender="iot.waterlogging",
            recipient="tier2.prediction",
            type=MessageType.ALERT,
            priority=Priority.CRITICAL,
            topic=Topic.IOT_TELEMETRY,
            module=Module.CYCLONE_FLOOD,
            payload={
                "kind": "waterlogging",
                "gateway": "iot.waterlogging",
                "sampled_at": "2026-06-08T00:00:00+00:00",
                "readings": [
                    {"site_id": "water-A-0", "zone": "zone-A", "water_level_m": 0.45},
                    {"site_id": "water-B-0", "zone": "zone-B", "water_level_m": 0.05},
                ],
            },
        )
    )
    pts = persistor.storage.telemetry.query_range(metric="water_level_m")
    assert len(pts) == 2
    p = persistor.storage.telemetry.latest("water-A-0", metric="water_level_m")
    assert p is not None and p.value == 0.45
    assert p.meta.get("zone") == "zone-A"


def test_gps_beacon_position_fix_persisted() -> None:
    """Status-only GPS beacons (no scalar field) still land a position row."""
    bus = InMemoryBus()
    persistor = StatePersistor(bus=bus, logger=DecisionLogger.null())
    seed_field_teams(bus)  # publishes a gps_beacon IOT_TELEMETRY frame
    pts = persistor.storage.telemetry.query_range(metric="gps_beacon")
    assert pts, "gps beacon readings should land position fixes"
    one = pts[0]
    assert "lat" in one.meta and "lon" in one.meta


def test_resource_plan_asset_state_is_retrievable() -> None:
    persistor = _drive_with_persistor()
    repo = persistor.storage.resources
    assets = repo.all_assets()
    assert assets, "RESOURCE_PLAN depots should be upserted as spatial assets"
    assert persistor.asset_writes >= len(assets)
    # A specific deploying asset is retrievable by id (depots carry vehicle ids).
    one = assets[0]
    fetched = repo.get_asset(one.asset_id)
    assert fetched is not None
    assert fetched.location.lat != 0.0 or fetched.location.lon != 0.0
    # nearest_asset works against the persisted spatial state.
    nearest = repo.nearest_asset(one.location, available_only=False)
    assert nearest is not None


def test_resource_plan_depot_directly_upserts_asset() -> None:
    """A RESOURCE_PLAN with explicit depots upserts a keyed, located asset."""
    bus = InMemoryBus()
    persistor = StatePersistor(bus=bus, logger=DecisionLogger.null())
    bus.publish(
        Message(
            sender="resource.allocator",
            recipient="broadcast",
            type=MessageType.INSTRUCTION,
            priority=Priority.HIGH,
            topic=Topic.RESOURCE_PLAN,
            module=Module.CYCLONE_FLOOD,
            incident_id="test:plan",
            payload={
                "kind": "resource_plan",
                "incident_id": "test:plan",
                "depots": [
                    {
                        "vehicle_id": "BOAT-99",
                        "depot": {"lat": 20.27, "lon": 85.84},
                        "capacity": 20,
                    }
                ],
            },
        )
    )
    fetched = persistor.storage.resources.get_asset("BOAT-99")
    assert fetched is not None
    assert fetched.location.lat == 20.27
    assert fetched.capacity == 20


def test_uses_injected_storage_instance() -> None:
    """A caller-supplied Storage is used (e.g. a shared/live handle)."""
    storage = Storage.in_memory()
    storage.telemetry.append(
        TelemetryPoint(sensor_id="pre", metric="seed", value=1.0)
    )
    bus = InMemoryBus()
    persistor = StatePersistor(bus=bus, logger=DecisionLogger.null(), storage=storage)
    assert persistor.storage is storage
    assert persistor.storage.telemetry.count() == 1
