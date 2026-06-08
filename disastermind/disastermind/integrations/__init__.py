"""Real backend integration adapters — offline-safe (PRD Step 9/10).

This package holds the *concrete* adapters that bridge DisasterMind to its
production backends, each with a deterministic stdlib fallback so the package
imports and the whole test-suite runs with NO external service and NO network:

  * :mod:`disastermind.integrations.kafka`   — :class:`KafkaRoundTrip` produce +
    consume of :class:`~disastermind.core.contracts.Message` dicts; degrades to
    an in-memory per-topic store when ``confluent_kafka``/the broker is absent.
  * :mod:`disastermind.integrations.sql`     — pure SQL string-builders + DDL for
    the PostGIS resource schema (assets/zones with geometry) and the TimescaleDB
    telemetry hypertable; no database needed to build or test them.
  * :mod:`disastermind.integrations.elastic` — Elasticsearch query-DSL builders
    for the audit index (match / term / range / bool); plain dicts, no client.
  * :mod:`disastermind.integrations.health`  — :func:`ping_backends` reporting
    ``absent`` / ``down`` / ``ok`` per backend; lazy imports, never raises.

The optional clients (``confluent_kafka``, ``psycopg``, ``elasticsearch``,
``minio``) are imported lazily inside functions, guarded by try/except.
"""
from __future__ import annotations

from . import elastic, health, kafka, sql
from .elastic import (
    AUDIT_INDEX,
    DEFAULT_TS_FIELD,
    audit_index_mapping,
    audit_search_body,
    bool_query,
    match_clause,
    query_string_clause,
    range_clause,
    term_clause,
    terms_clause,
)
from .health import ABSENT, BACKENDS, DOWN, OK, ping_backends
from .kafka import (
    DEFAULT_BOOTSTRAP,
    KafkaRoundTrip,
    frame_to_dict,
    message_to_frame,
)
from .sql import (
    ASSETS_TABLE,
    ASSET_COLUMNS,
    SRID,
    TELEMETRY_TABLE,
    TELEMETRY_COLUMNS,
    ZONES_TABLE,
    ZONE_SCALAR_COLUMNS,
    ZONE_VULN_COLUMNS,
    all_assets_sql,
    all_zones_sql,
    create_assets_table_ddl,
    create_postgis_extension_ddl,
    create_telemetry_hypertable_ddl,
    create_telemetry_table_ddl,
    create_timescale_extension_ddl,
    create_zones_table_ddl,
    get_asset_sql,
    get_zone_sql,
    insert_telemetry_sql,
    latest_telemetry_sql,
    nearest_assets_sql,
    query_telemetry_range_sql,
    schema_ddl,
    schema_sql,
    set_asset_available_sql,
    upsert_asset_sql,
    upsert_zone_sql,
    zones_within_sql,
)

__all__ = [
    # submodules
    "kafka",
    "sql",
    "elastic",
    "health",
    # kafka
    "KafkaRoundTrip",
    "message_to_frame",
    "frame_to_dict",
    "DEFAULT_BOOTSTRAP",
    # sql — constants
    "ASSETS_TABLE",
    "ZONES_TABLE",
    "TELEMETRY_TABLE",
    "SRID",
    "ASSET_COLUMNS",
    "ZONE_SCALAR_COLUMNS",
    "ZONE_VULN_COLUMNS",
    "TELEMETRY_COLUMNS",
    # sql — DDL
    "create_postgis_extension_ddl",
    "create_timescale_extension_ddl",
    "create_assets_table_ddl",
    "create_zones_table_ddl",
    "create_telemetry_table_ddl",
    "create_telemetry_hypertable_ddl",
    "schema_ddl",
    "schema_sql",
    # sql — builders
    "upsert_asset_sql",
    "get_asset_sql",
    "all_assets_sql",
    "set_asset_available_sql",
    "nearest_assets_sql",
    "upsert_zone_sql",
    "get_zone_sql",
    "all_zones_sql",
    "zones_within_sql",
    "insert_telemetry_sql",
    "query_telemetry_range_sql",
    "latest_telemetry_sql",
    # elastic
    "audit_search_body",
    "audit_index_mapping",
    "bool_query",
    "match_clause",
    "query_string_clause",
    "term_clause",
    "terms_clause",
    "range_clause",
    "AUDIT_INDEX",
    "DEFAULT_TS_FIELD",
    # health
    "ping_backends",
    "ABSENT",
    "DOWN",
    "OK",
    "BACKENDS",
]
