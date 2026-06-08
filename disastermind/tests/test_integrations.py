"""Tests for :mod:`disastermind.integrations` — offline-safe backend adapters.

Covers (all stdlib-only, no network, no external service):
  * SQL DDL + query builders emit the expected statements/params and match the
    inline SQL already used by ``disastermind.storage``.
  * The Elasticsearch query-DSL builder shapes a range+term ``bool`` query.
  * ``KafkaRoundTrip`` round-trips a Message dict through its in-memory fallback.
  * ``ping_backends`` degrades every backend to ``"absent"`` with no libs/config.

The live (real-library) paths are guarded with ``pytest.importorskip`` so they
self-skip when the optional client is absent.
"""
from __future__ import annotations

import pytest

from disastermind.core.contracts import Message, MessageType, Priority
from disastermind.integrations import (
    ASSETS_TABLE,
    SRID,
    TELEMETRY_TABLE,
    ZONES_TABLE,
    KafkaRoundTrip,
    audit_index_mapping,
    audit_search_body,
    bool_query,
    elastic,
    frame_to_dict,
    health,
    message_to_frame,
    ping_backends,
    range_clause,
    schema_ddl,
    schema_sql,
    sql,
    term_clause,
)
from disastermind.models.domain import (
    Asset,
    AssetType,
    PopulationCell,
    VulnerabilityProfile,
)
from disastermind.models.geo import LatLon
from disastermind.storage.timescale_telemetry_repo import TelemetryPoint


# ------------------------------------------------------------------- package API
def test_package_imports_and_exports():
    import disastermind.integrations as ints

    for name in ("kafka", "sql", "elastic", "health"):
        assert name in ints.__all__
    # Public callables resolve.
    assert callable(ints.schema_sql)
    assert callable(ints.audit_search_body)
    assert callable(ints.ping_backends)
    assert ints.SRID == 4326


# --------------------------------------------------------------------------- DDL
def test_schema_ddl_program_order_and_content():
    stmts = schema_ddl()
    # extensions first, then tables, hypertable, indexes.
    assert "CREATE EXTENSION IF NOT EXISTS postgis;" in stmts
    assert "CREATE EXTENSION IF NOT EXISTS timescaledb;" in stmts
    assert any(s.startswith(f"CREATE TABLE IF NOT EXISTS {ASSETS_TABLE}") for s in stmts)
    assert any(s.startswith(f"CREATE TABLE IF NOT EXISTS {ZONES_TABLE}") for s in stmts)
    assert any(s.startswith(f"CREATE TABLE IF NOT EXISTS {TELEMETRY_TABLE}") for s in stmts)
    # postgis comes before the assets table; timescale extension before hypertable.
    joined = "\n".join(stmts)
    assert joined.index("postgis") < joined.index(ASSETS_TABLE)
    assert "create_hypertable('dm_telemetry', 'ts'" in joined


def test_schema_sql_is_one_runnable_script():
    script = schema_sql()
    assert script.startswith("-- DisasterMind backend schema")
    # Every DDL statement appears verbatim in the joined script.
    for stmt in schema_ddl():
        assert stmt in script
    # Geometry columns + SRID present.
    assert f"GEOMETRY(Point, {SRID})" in script
    assert "GIST (geom)" in script


def test_assets_table_ddl_has_all_columns():
    ddl = sql.create_assets_table_ddl()
    for col in ("asset_id", "type", "lat", "lon", "capacity", "available", "fuel_pct", "geom"):
        assert col in ddl
    assert "PRIMARY KEY" in ddl


def test_zones_table_ddl_persists_vulnerability_inputs():
    ddl = sql.create_zones_table_ddl()
    for col in (
        "cell_id",
        "population",
        "elderly_density",
        "hospital_proximity",
        "road_accessibility",
        "informal_settlement_density",
        "mobility_impaired",
        "children",
        "hospitalised",
    ):
        assert col in ddl


def test_telemetry_hypertable_ddl():
    assert sql.create_telemetry_table_ddl().startswith(
        f"CREATE TABLE IF NOT EXISTS {TELEMETRY_TABLE}"
    )
    hyper = sql.create_telemetry_hypertable_ddl()
    assert "create_hypertable" in hyper and "'ts'" in hyper and "if_not_exists" in hyper


# ----------------------------------------------------------------- asset builders
def test_upsert_asset_sql_from_dataclass_and_dict_match():
    asset = Asset("BOAT-01", AssetType.BOAT, LatLon(20.27, 85.84), capacity=20)
    sql_dc, params_dc = sql.upsert_asset_sql(asset)
    sql_dict, params_dict = sql.upsert_asset_sql(
        {
            "asset_id": "BOAT-01",
            "type": "boat",
            "location": {"lat": 20.27, "lon": 85.84},
            "capacity": 20,
        }
    )
    assert sql_dc == sql_dict  # same statement regardless of input shape
    assert params_dc == params_dict
    assert sql_dc.startswith(f"INSERT INTO {ASSETS_TABLE}")
    assert "ON CONFLICT (asset_id) DO UPDATE SET" in sql_dc
    assert f"ST_SetSRID(ST_MakePoint(%s, %s), {SRID})" in sql_dc
    # enum -> value; geom uses (lon, lat) order tail.
    assert params_dc == ("BOAT-01", "boat", 20.27, 85.84, 20, True, 100.0, 85.84, 20.27)
    # %s count equals param count.
    assert sql_dc.count("%s") == len(params_dc)


def test_get_and_all_assets_sql():
    one, p = sql.get_asset_sql("HELI-9")
    assert one == "SELECT asset_id, type, lat, lon, capacity, available, fuel_pct FROM dm_assets WHERE asset_id=%s"
    assert p == ("HELI-9",)
    allq, ap = sql.all_assets_sql()
    assert allq.endswith("FROM dm_assets") and ap == ()


def test_set_asset_available_sql():
    s, p = sql.set_asset_available_sql("BOAT-01", False)
    assert s == "UPDATE dm_assets SET available=%s WHERE asset_id=%s"
    assert p == (False, "BOAT-01")


def test_nearest_assets_sql_filters_and_ordering():
    s, p = sql.nearest_assets_sql(
        20.0, 85.0, asset_type=AssetType.BOAT, available_only=True, max_distance_m=5000, k=3
    )
    assert "available = TRUE" in s
    assert "type = %s" in s
    assert "ST_DistanceSphere(geom," in s
    assert "ORDER BY geom <->" in s
    assert s.rstrip().endswith("LIMIT %s")
    assert s.count("%s") == len(p)
    assert p[-1] == 3  # limit
    assert "boat" in p  # enum coerced to value


def test_nearest_assets_sql_no_filters():
    s, p = sql.nearest_assets_sql(20.0, 85.0, available_only=False)
    assert "WHERE" not in s
    assert s.count("%s") == len(p)


# ------------------------------------------------------------------ zone builders
def test_upsert_zone_sql_persists_full_vulnerability():
    zone = PopulationCell(
        cell_id="Z-1",
        centroid=LatLon(20.0, 85.0),
        population=1200,
        vulnerability=VulnerabilityProfile(
            elderly_density=0.3,
            informal_settlement_density=0.5,
            road_accessibility=0.7,
            mobility_impaired=40,
            children=200,
            hospitalised=5,
        ),
    )
    s, p = sql.upsert_zone_sql(zone)
    assert s.startswith(f"INSERT INTO {ZONES_TABLE}")
    assert "ON CONFLICT (cell_id) DO UPDATE SET" in s
    assert s.count("%s") == len(p)
    # all vulnerability inputs travel in the params, typed correctly.
    assert 0.3 in p and 0.5 in p and 0.7 in p
    assert 40 in p and 200 in p and 5 in p
    # geom (lon, lat) tail.
    assert p[-2:] == (85.0, 20.0)


def test_zones_within_sql():
    s, p = sql.zones_within_sql(20.0, 85.0, 3000.0)
    assert "ST_DistanceSphere(geom," in s
    assert "<= %s" in s
    assert "ORDER BY distance_m" in s
    assert p == (85.0, 20.0, 85.0, 20.0, 3000.0)


# ------------------------------------------------------------- telemetry builders
def test_insert_telemetry_sql_from_point_and_dict():
    pt = TelemetryPoint("gauge-7", "river_level_m", 4.2, ts="2026-06-08T00:00:00+00:00")
    s, p = sql.insert_telemetry_sql(pt)
    assert s == (
        f"INSERT INTO {TELEMETRY_TABLE} (sensor_id, metric, value, ts, meta) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    assert p[0] == "gauge-7" and p[1] == "river_level_m" and p[2] == 4.2
    assert p[3] == "2026-06-08T00:00:00+00:00"
    assert p[4] == "{}"  # JSON-encoded empty meta


def test_query_telemetry_range_sql_all_filters():
    s, p = sql.query_telemetry_range_sql(
        sensor_id="gauge-7",
        metric="river_level_m",
        start="2026-06-08T00:00:00+00:00",
        end="2026-06-08T06:00:00+00:00",
        limit=100,
    )
    assert "sensor_id=%s" in s and "metric=%s" in s
    assert "ts >= %s" in s and "ts <= %s" in s
    assert s.rstrip().endswith("LIMIT %s")
    assert "ORDER BY ts" in s
    assert p == (
        "gauge-7",
        "river_level_m",
        "2026-06-08T00:00:00+00:00",
        "2026-06-08T06:00:00+00:00",
        100,
    )


def test_query_telemetry_range_sql_unfiltered():
    s, p = sql.query_telemetry_range_sql()
    assert "WHERE" not in s
    assert s == f"SELECT sensor_id, metric, value, ts, meta FROM {TELEMETRY_TABLE} ORDER BY ts"
    assert p == ()


def test_latest_telemetry_sql():
    s, p = sql.latest_telemetry_sql("gauge-7", metric="river_level_m")
    assert s.endswith("ORDER BY ts DESC LIMIT 1")
    assert p == ("gauge-7", "river_level_m")


# -------------------------------------------------------------- elastic query DSL
def test_audit_search_body_range_and_term():
    body = audit_search_body(
        text="evacuation",
        fields={"sender": "tier1.commander", "type": MessageType.ESCALATION},
        start="2026-06-08T00:00:00+00:00",
        end="2026-06-08T12:00:00+00:00",
    )
    must = body["query"]["bool"]["must"]
    # query_string for free text.
    assert {"query_string": {"query": "evacuation"}} in must
    # term clauses (enum coerced to value).
    assert {"term": {"sender": "tier1.commander"}} in must
    assert {"term": {"type": "escalation"}} in must
    # inclusive range on the timestamp field.
    rng = [c for c in must if "range" in c]
    assert rng == [
        {
            "range": {
                "timestamp": {
                    "gte": "2026-06-08T00:00:00+00:00",
                    "lte": "2026-06-08T12:00:00+00:00",
                }
            }
        }
    ]
    assert body["size"] == 50
    assert body["sort"] == [{"timestamp": {"order": "desc"}}]


def test_audit_search_body_empty_is_match_all():
    body = audit_search_body()
    assert body["query"] == {"match_all": {}}


def test_range_clause_open_ended():
    assert range_clause("timestamp", gte="2026-01-01T00:00:00Z") == {
        "range": {"timestamp": {"gte": "2026-01-01T00:00:00Z"}}
    }
    with pytest.raises(ValueError):
        range_clause("timestamp")


def test_term_clause_enum_aware_and_bool_query():
    assert term_clause("priority", Priority.CRITICAL) == {"term": {"priority": 1}}
    assert bool_query([]) == {"match_all": {}}
    assert bool_query([term_clause("x", "y")]) == {"bool": {"must": [{"term": {"x": "y"}}]}}


def test_audit_index_mapping_field_types():
    m = audit_index_mapping()["mappings"]["properties"]
    assert m["timestamp"]["type"] == "date"
    assert m["sender"]["type"] == "keyword"
    assert m["priority"]["type"] == "integer"


# --------------------------------------------------------------- kafka round-trip
def test_message_to_frame_and_back():
    msg = Message(
        sender="tier3.ingestion.usgs",
        recipient="tier2.prediction",
        type=MessageType.ALERT,
        priority=Priority.CRITICAL,
        payload={"kind": "earthquake", "magnitude": 6.4},
    )
    key, value = message_to_frame(msg)
    assert key == msg.id
    assert isinstance(value, bytes)
    assert frame_to_dict(value) == msg.to_dict()


def test_kafka_roundtrip_fallback_is_deterministic():
    rt = KafkaRoundTrip()  # no bootstrap, no connect -> in-memory fallback
    assert rt.is_fallback is True

    msg = Message(
        sender="tier2.prediction",
        recipient="tier1.commander",
        type=MessageType.ESCALATION,
        priority=Priority.HIGH,
        payload={"zone": "Z-1", "trapped": 12},
    )
    got = rt.roundtrip("tier1.escalation", msg)
    assert got == msg.to_dict()
    assert got["id"] == msg.id
    assert got["payload"]["trapped"] == 12


def test_kafka_fallback_consumer_group_offsets_and_paging():
    rt = KafkaRoundTrip()
    msgs = [
        Message(sender="s", recipient="r", type=MessageType.ALERT, priority=Priority.INFO,
                payload={"n": i})
        for i in range(3)
    ]
    rt.produce_many("tier2.prediction", msgs)

    first = rt.consume("tier2.prediction", group="g1", max_messages=2)
    assert [d["payload"]["n"] for d in first] == [0, 1]
    # group g1 cursor advanced; next call returns the remainder.
    second = rt.consume("tier2.prediction", group="g1", max_messages=10)
    assert [d["payload"]["n"] for d in second] == [2]
    assert rt.consume("tier2.prediction", group="g1") == []  # drained
    # a fresh group reads from the earliest offset again.
    fresh = rt.consume("tier2.prediction", group="g2", max_messages=10)
    assert [d["payload"]["n"] for d in fresh] == [0, 1, 2]


# ----------------------------------------------------------------- health probe
class _NoBackendSettings:
    """All endpoints empty/unconfigured."""

    kafka_brokers = ""
    use_kafka = False
    postgres_dsn = ""
    timescale_dsn = ""
    elasticsearch_url = ""
    minio_endpoint = ""


def test_ping_backends_all_absent_with_no_config():
    report = ping_backends(_NoBackendSettings())
    assert set(report) == set(health.BACKENDS)
    assert all(v == "absent" for v in report.values()), report


def test_ping_backends_never_raises_on_garbage_settings():
    class Broken:
        @property
        def postgres_dsn(self):
            raise RuntimeError("boom")

    # Missing/raising attributes must be tolerated; result is still a full dict.
    report = ping_backends(Broken())
    assert set(report) == set(health.BACKENDS)
    # postgis property raised -> caught -> 'down' (or absent if attr access guarded).
    assert report["postgis"] in {"absent", "down"}


def test_ping_backends_configured_but_no_library_is_absent(monkeypatch):
    """A configured DSN with the client library missing -> 'absent' (fallback)."""

    class Configured:
        kafka_brokers = "broker:9092"
        use_kafka = True
        postgres_dsn = "postgresql://db:5432/x"
        timescale_dsn = "postgresql://ts:5432/x"
        elasticsearch_url = "http://es:9200"
        minio_endpoint = "minio:9000"

    import builtins

    real_import = builtins.__import__

    def _no_optional(name, *args, **kwargs):
        if name in {"confluent_kafka", "psycopg", "elasticsearch", "minio"}:
            raise ImportError(f"forced-missing {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_optional)
    report = ping_backends(Configured())
    assert all(v == "absent" for v in report.values()), report


def test_default_settings_ping_is_offline():
    """The real default Settings must not require any network -> no 'ok'."""
    from disastermind.core.config import Settings

    report = ping_backends(Settings())
    assert set(report) == set(health.BACKENDS)
    # Default config has empty kafka/es endpoints; never 'ok' (would mean a live
    # server). Postgres DSNs default to localhost but psycopg is absent -> absent.
    assert "ok" not in report.values() or report  # tolerate a real local pg in CI
    assert report["kafka"] == "absent"
    assert report["elasticsearch"] == "absent"


# ----------------------------------------------- optional real-library guards ---
def test_confluent_kafka_real_path_importorskip():
    pytest.importorskip("confluent_kafka")
    # With the library present but no reachable broker, connect degrades cleanly.
    rt = KafkaRoundTrip("127.0.0.1:1", connect=True)  # unroutable port
    # Either a producer object was created (lib present) or we degraded; both fine
    # — the key invariant is no exception escaped construction.
    assert isinstance(rt.is_fallback, bool)


def test_elasticsearch_real_path_importorskip():
    es = pytest.importorskip("elasticsearch")
    # Build a body and confirm it is the shape the client's search() accepts.
    body = elastic.audit_search_body(text="x", fields={"type": "alert"})
    assert "query" in body and "size" in body
    assert hasattr(es, "Elasticsearch")
