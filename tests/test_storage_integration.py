"""Storage <-> integrations SQL/DSL convergence tests (PRD Step 9 persistence).

The storage repositories' *live* (psycopg / elasticsearch) backend branches used
to embed ad-hoc inline SQL and query bodies. They have been converged onto the
single canonical builder modules :mod:`disastermind.integrations.sql` (PostGIS /
Timescale DDL + statements) and :mod:`disastermind.integrations.elastic` (audit
query DSL), so the SQL/DSL lives in exactly one place.

These tests assert three things, all fully offline (no database, no network):

  1. the integrations builders emit the expected DDL/SQL/DSL shapes;
  2. the storage repos' live paths *call those builders* with the right
     arguments and execute exactly what they return — verified by driving the
     live branch with a fake DB connection / ES client (the fallback paths and
     public signatures are exercised by the existing ``test_storage.py``);
  3. ``deploy/sql/schema.sql`` on disk matches ``integrations.sql.schema_sql()``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from disastermind.integrations import elastic, sql
from disastermind.models.domain import (
    Asset,
    AssetType,
    PopulationCell,
    VulnerabilityProfile,
)
from disastermind.models.geo import LatLon
from disastermind.storage import (
    ElasticsearchAuditRepo,
    PostgisResourceRepo,
    TelemetryPoint,
    TimescaleTelemetryRepo,
)


# --------------------------------------------------------------------- helpers
def _fake_conn_with_cursor():
    """A psycopg-shaped connection whose ``with conn.cursor() as cur`` yields a mock."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = None
    cur.fetchall.return_value = []
    return conn, cur


# ============================================================ builder shapes ==
def test_schema_sql_emits_expected_ddl():
    script = sql.schema_sql()
    # extensions + the three canonical tables + hypertable + indexes, in order.
    assert "CREATE EXTENSION IF NOT EXISTS postgis;" in script
    assert "CREATE EXTENSION IF NOT EXISTS timescaledb;" in script
    assert "CREATE TABLE IF NOT EXISTS dm_assets" in script
    assert "CREATE TABLE IF NOT EXISTS dm_zones" in script
    assert "CREATE TABLE IF NOT EXISTS dm_telemetry" in script
    assert "create_hypertable('dm_telemetry', 'ts'" in script
    assert "GEOMETRY(Point, 4326)" in script
    # one runnable script == newline-joined program with the header banner.
    assert script.startswith("-- DisasterMind backend schema")
    for stmt in sql.schema_ddl():
        assert stmt in script


def test_asset_and_zone_query_builders_shape():
    one_sql, one_params = sql.get_asset_sql("BOAT-9")
    assert one_sql == "SELECT asset_id, type, lat, lon, capacity, available, fuel_pct FROM dm_assets WHERE asset_id=%s"
    assert one_params == ("BOAT-9",)

    all_sql, all_params = sql.all_assets_sql()
    assert all_sql.startswith("SELECT") and all_sql.endswith("FROM dm_assets")
    assert all_params == ()

    up_sql, up_params = sql.upsert_zone_sql(
        PopulationCell("c1", LatLon(20.3, 85.8), population=42)
    )
    assert up_sql.startswith("INSERT INTO dm_zones")
    assert "ON CONFLICT (cell_id) DO UPDATE SET" in up_sql
    assert up_sql.count("%s") == len(up_params)  # placeholder/param parity


def test_telemetry_query_builder_shape():
    s, p = sql.query_telemetry_range_sql(
        sensor_id="g1", metric="river_level_m", start="a", end="b", limit=10
    )
    assert s == (
        "SELECT sensor_id, metric, value, ts, meta FROM dm_telemetry "
        "WHERE sensor_id=%s AND metric=%s AND ts >= %s AND ts <= %s ORDER BY ts LIMIT %s"
    )
    assert p == ("g1", "river_level_m", "a", "b", 10)


def test_audit_search_body_shape():
    body = elastic.audit_search_body(
        text="evacuate", fields={"sender": "commander"}, start="a", end="b"
    )
    must = body["query"]["bool"]["must"]
    assert {"query_string": {"query": "evacuate"}} in must
    assert {"term": {"sender": "commander"}} in must
    assert {"range": {"timestamp": {"gte": "a", "lte": "b"}}} in must
    assert body["size"] == 50


# ===================================================== repos call the builders ==
def test_postgis_live_path_uses_integrations_sql_builders():
    repo = PostgisResourceRepo()
    conn, cur = _fake_conn_with_cursor()
    repo._conn = conn
    assert repo.is_fallback is False  # now on the simulated live backend

    asset = Asset("BOAT-01", AssetType.BOAT, LatLon(20.27, 85.84), capacity=20)
    repo.upsert_asset(asset)
    got_sql, got_params = cur.execute.call_args[0]
    exp_sql, exp_params = sql.upsert_asset_sql(asset)
    assert got_sql == exp_sql
    assert tuple(got_params) == tuple(exp_params)

    repo.get_asset("BOAT-01")
    assert cur.execute.call_args[0] == sql.get_asset_sql("BOAT-01")

    repo.all_assets()
    got_sql, _ = cur.execute.call_args[0]
    assert got_sql == sql.all_assets_sql()[0]

    zone = PopulationCell(
        "c1", LatLon(20.3, 85.8), population=500,
        vulnerability=VulnerabilityProfile(elderly_density=0.4),
    )
    repo.upsert_zone(zone)
    got_sql, got_params = cur.execute.call_args[0]
    exp_sql, exp_params = sql.upsert_zone_sql(zone)
    assert got_sql == exp_sql
    assert tuple(got_params) == tuple(exp_params)

    repo.get_zone("c1")
    assert cur.execute.call_args[0] == sql.get_zone_sql("c1")
    repo.all_zones()
    assert cur.execute.call_args[0][0] == sql.all_zones_sql()[0]


def test_postgis_live_path_delegates_via_patched_builder(monkeypatch):
    """Patching the builder is observed by the live repo path (single source)."""
    repo = PostgisResourceRepo()
    conn, cur = _fake_conn_with_cursor()
    repo._conn = conn
    sentinel = ("SENTINEL SQL", ("p",))
    monkeypatch.setattr(sql, "all_assets_sql", lambda: sentinel)
    repo.all_assets()
    assert cur.execute.call_args[0] == sentinel


def test_timescale_live_path_uses_integrations_sql_builders():
    repo = TimescaleTelemetryRepo()
    conn, cur = _fake_conn_with_cursor()
    repo._conn = conn
    assert repo.is_fallback is False

    pt = TelemetryPoint("g1", "river_level_m", 5.0, ts="2026-06-08T10:00:00+00:00")
    repo.append(pt)
    got_sql, got_params = cur.execute.call_args[0]
    exp_sql, exp_params = sql.insert_telemetry_sql(pt)
    assert got_sql == exp_sql
    assert tuple(got_params) == tuple(exp_params)

    repo.query_range(sensor_id="g1", metric="river_level_m", start="a", end="b", limit=3)
    got_sql, got_params = cur.execute.call_args[0]
    exp_sql, exp_params = sql.query_telemetry_range_sql("g1", "river_level_m", "a", "b", 3)
    assert got_sql == exp_sql
    assert tuple(got_params) == tuple(exp_params)

    repo.latest("g1", "river_level_m")
    got_sql, got_params = cur.execute.call_args[0]
    exp_sql, exp_params = sql.latest_telemetry_sql("g1", "river_level_m")
    assert got_sql == exp_sql
    assert tuple(got_params) == tuple(exp_params)


def test_elasticsearch_live_path_uses_integrations_dsl_builder():
    repo = ElasticsearchAuditRepo()
    es = MagicMock()
    es.search.return_value = {"hits": {"hits": [{"_source": {"sender": "commander"}}]}}
    repo._es = es
    assert repo.is_fallback is False

    out = repo.search(text="evacuate", fields={"sender": "commander"}, start="a", end="b")
    assert out == [{"sender": "commander"}]
    body = es.search.call_args.kwargs["body"]
    assert body == elastic.audit_search_body(
        "evacuate", fields={"sender": "commander"}, start="a", end="b",
        ts_field="timestamp", size=50,
    )


def test_elasticsearch_live_path_delegates_via_patched_builder(monkeypatch):
    repo = ElasticsearchAuditRepo()
    es = MagicMock()
    es.search.return_value = {"hits": {"hits": []}}
    repo._es = es
    sentinel = {"query": {"match_all": {}}, "size": 7, "_sentinel": True}
    monkeypatch.setattr(elastic, "audit_search_body", lambda *a, **k: sentinel)
    repo.search(text="x")
    assert es.search.call_args.kwargs["body"] == sentinel


# ====================================================== schema.sql on disk ==
def test_deploy_schema_sql_matches_integrations_output():
    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "deploy" / "sql" / "schema.sql"
    assert schema_path.is_file(), f"missing {schema_path}"
    on_disk = schema_path.read_text()
    assert on_disk == sql.schema_sql(), (
        "deploy/sql/schema.sql is stale; regenerate from "
        "disastermind.integrations.sql.schema_sql()"
    )


# ============================================== fallback behaviour unchanged ==
def test_fallback_paths_never_import_integrations(monkeypatch):
    """Default (no DSN) repos must not touch the integrations builders at all."""
    boom = MagicMock(side_effect=AssertionError("fallback must not call the builder"))
    monkeypatch.setattr(sql, "upsert_asset_sql", boom)
    monkeypatch.setattr(sql, "insert_telemetry_sql", boom)
    monkeypatch.setattr(elastic, "audit_search_body", boom)

    presource = PostgisResourceRepo()
    assert presource.is_fallback is True
    presource.upsert_asset(Asset("X", AssetType.BOAT, LatLon(0, 0)))
    assert presource.get_asset("X") is not None

    telem = TimescaleTelemetryRepo()
    telem.append(TelemetryPoint("g", "m", 1.0))
    assert telem.count() == 1

    audit = ElasticsearchAuditRepo()
    audit.index_record({"sender": "x", "timestamp": "2026-06-08T00:00:00+00:00"})
    assert audit.search(text="x")  # uses the offline engine, not the DSL builder


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
