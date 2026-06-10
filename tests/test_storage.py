"""Persistence layer fallback tests (PRD Step 9 storage).

The storage agent hit a session limit before shipping its test file; this covers
the four repositories' in-memory/local fallbacks (no external service, no
network) plus the ``Storage`` facade. All four backends degrade to fallback when
constructed without a DSN/URL/endpoint.
"""
from __future__ import annotations

from disastermind.core.contracts import Message, MessageType, Priority, Topic
from disastermind.models.domain import (
    Asset,
    AssetType,
    PopulationCell,
    VulnerabilityProfile,
)
from disastermind.models.geo import LatLon
from disastermind.storage import (
    ElasticsearchAuditRepo,
    MinioArtifactStore,
    PostgisResourceRepo,
    Storage,
    TelemetryPoint,
    TimescaleTelemetryRepo,
)


# --------------------------------------------------------------- PostGIS (spatial)
def test_postgis_fallback_upsert_get_and_nearest():
    repo = PostgisResourceRepo()
    assert repo.is_fallback is True

    repo.upsert_assets(
        [
            Asset("BOAT-01", AssetType.BOAT, LatLon(20.27, 85.84), capacity=20),
            {  # dict payload (as it crosses the bus) must coerce too
                "asset_id": "BOAT-02",
                "type": "boat",
                "location": {"lat": 20.50, "lon": 86.10},
                "capacity": 15,
            },
            Asset("HELI-01", AssetType.HELICOPTER, LatLon(20.28, 85.85), available=False),
        ]
    )
    assert repo.get_asset("BOAT-01").capacity == 20
    assert len(repo.all_assets()) == 3

    near = repo.nearest_asset(LatLon(20.27, 85.84), asset_type=AssetType.BOAT)
    assert near is not None
    asset, dist = near
    assert asset.asset_id == "BOAT-01" and dist < 50  # essentially co-located

    # available_only excludes the grounded helicopter
    assert repo.nearest_asset(LatLon(20.28, 85.85), asset_type=AssetType.HELICOPTER) is None
    # max_distance filters out the far boat
    far = repo.nearest_assets(LatLon(20.27, 85.84), asset_type=AssetType.BOAT, max_distance_m=1000)
    assert [a.asset_id for a, _ in far] == ["BOAT-01"]


def test_postgis_fallback_zones():
    repo = PostgisResourceRepo()
    repo.upsert_zone(
        PopulationCell(
            "100m:1:1",
            LatLon(20.30, 85.80),
            population=500,
            vulnerability=VulnerabilityProfile(elderly_density=0.4),
        )
    )
    repo.upsert_zone({"cell_id": "100m:1:2", "centroid": {"lat": 21.0, "lon": 86.0}, "population": 100})
    nz = repo.nearest_zone(LatLon(20.30, 85.80))
    assert nz is not None and nz[0].cell_id == "100m:1:1"
    within = repo.zones_within(LatLon(20.30, 85.80), radius_m=2000)
    assert [z.cell_id for z, _ in within] == ["100m:1:1"]


# ------------------------------------------------------------ Timescale (telemetry)
def test_timescale_fallback_append_query_range_and_latest():
    repo = TimescaleTelemetryRepo()
    assert repo.is_fallback is True
    # intentionally out-of-order to exercise the re-sort path
    repo.append_many(
        [
            TelemetryPoint("gauge-1", "river_level_m", 5.0, ts="2026-06-08T10:00:00+00:00"),
            {"sensor_id": "gauge-1", "metric": "river_level_m", "value": 7.0, "ts": "2026-06-08T12:00:00+00:00"},
            TelemetryPoint("gauge-1", "river_level_m", 6.0, ts="2026-06-08T11:00:00+00:00"),
            TelemetryPoint("gauge-2", "river_level_m", 1.0, ts="2026-06-08T11:30:00+00:00"),
        ]
    )
    assert repo.count() == 4
    window = repo.query_range(
        sensor_id="gauge-1",
        metric="river_level_m",
        start="2026-06-08T10:30:00+00:00",
        end="2026-06-08T11:59:00+00:00",
    )
    assert [p.value for p in window] == [6.0]  # only the 11:00 reading, sorted & filtered
    assert repo.latest("gauge-1").value == 7.0


# ----------------------------------------------------------- Elasticsearch (audit)
def test_elasticsearch_fallback_index_and_search():
    repo = ElasticsearchAuditRepo()
    assert repo.is_fallback is True
    msg = Message(
        sender="commander",
        recipient="dispatch.router",
        type=MessageType.INSTRUCTION,
        priority=Priority.CRITICAL,
        topic=Topic.DISPATCH,
        payload={"channel": "sms", "body": "evacuate sector 4"},
        reasoning=["within autonomous authority"],
    )
    repo.index_record(msg)
    repo.index_record({"sender": "field_coordinator", "timestamp": "2026-06-08T09:00:00+00:00", "note": "team idle"})
    assert repo.count() == 2

    assert len(repo.search(text="evacuate")) == 1
    assert len(repo.search(fields={"sender": "commander"})) == 1
    assert len(repo.search(start="2026-06-08T08:00:00+00:00", end="2026-06-08T09:30:00+00:00")) == 1


# --------------------------------------------------------------- MinIO (artifacts)
def test_minio_fallback_put_get_exists_delete(tmp_path):
    store = MinioArtifactStore(base_dir=str(tmp_path))
    assert store.is_fallback is True
    key = "models/flood/unet-v1.bin"  # nested key must survive the local fs
    store.put(key, b"\x00\x01weights")
    assert store.exists(key) is True
    assert store.get(key) == b"\x00\x01weights"

    import pytest

    with pytest.raises(KeyError):
        store.get("missing/key")
    with pytest.raises(TypeError):
        store.put("bad", "not-bytes")  # type: ignore[arg-type]

    assert store.delete(key) is True
    assert store.exists(key) is False


# ------------------------------------------------------------------- facade
def test_storage_facade_in_memory_all_fallback():
    s = Storage.in_memory()
    assert s.all_fallback is True
    # from_settings defaults to offline (never connects)
    assert Storage.from_settings().all_fallback is True
    s.resources.upsert_asset(Asset("X", AssetType.NDRF_TEAM, LatLon(0, 0)))
    assert s.resources.get_asset("X") is not None
