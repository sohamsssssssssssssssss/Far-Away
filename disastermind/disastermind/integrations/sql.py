"""Pure SQL string-builders + DDL for the DisasterMind backend schema.

PRD Step 9 (Decision Logging & Persistence) and Step 6 (sensor telemetry)
require a PostgreSQL+PostGIS resource store and a TimescaleDB telemetry
hypertable. The existing repositories in :mod:`disastermind.storage` embed their
own ad-hoc SQL inline; this module factors the *schema* (DDL) and the common
write/read statements into a single, dependency-free place that those repos
*could adopt* without change in behaviour. It is deliberately standalone — it
does NOT import or modify :mod:`disastermind.storage`.

Every function here returns a ``(sql, params)`` tuple (or, for DDL, a plain
``str``) — there is no database connection anywhere in this module, so the
builders are fully testable with no server (PRD Step 10 graceful degradation).
Parameter placeholders use psycopg's ``%s`` style to match the existing
:class:`disastermind.storage.postgis_resource_repo.PostgisResourceRepo`.

Column layout mirrors the dataclasses in :mod:`disastermind.models.domain`:
  * :class:`~disastermind.models.domain.Asset`           -> ``dm_assets``
  * :class:`~disastermind.models.domain.PopulationCell`  +
    :class:`~disastermind.models.domain.VulnerabilityProfile` -> ``dm_zones``
  * :class:`disastermind.storage.timescale_telemetry_repo.TelemetryPoint`
                                                          -> ``dm_telemetry``
"""
from __future__ import annotations

from typing import Any

# Table names — kept identical to the inline SQL already used by storage so the
# DDL emitted here is drop-in compatible with the existing repositories.
ASSETS_TABLE = "dm_assets"
ZONES_TABLE = "dm_zones"
TELEMETRY_TABLE = "dm_telemetry"

# WGS-84; matches ST_SetSRID(..., 4326) used throughout the spatial repo.
SRID = 4326

# Ordered column lists (single source of truth for the builders below).
ASSET_COLUMNS: tuple[str, ...] = (
    "asset_id",
    "type",
    "lat",
    "lon",
    "capacity",
    "available",
    "fuel_pct",
)
ZONE_SCALAR_COLUMNS: tuple[str, ...] = (
    "cell_id",
    "lat",
    "lon",
    "population",
)
# Vulnerability weighting inputs (PRD Step 4 equity constraint) persisted per zone.
ZONE_VULN_COLUMNS: tuple[str, ...] = (
    "elderly_density",
    "hospital_proximity",
    "road_accessibility",
    "informal_settlement_density",
    "mobility_impaired",
    "children",
    "hospitalised",
)
TELEMETRY_COLUMNS: tuple[str, ...] = (
    "sensor_id",
    "metric",
    "value",
    "ts",
    "meta",
)


# --------------------------------------------------------------------------- DDL
def create_postgis_extension_ddl() -> str:
    """Enable PostGIS (idempotent). Required before the geometry columns below."""
    return "CREATE EXTENSION IF NOT EXISTS postgis;"


def create_timescale_extension_ddl() -> str:
    """Enable TimescaleDB (idempotent). Required for the telemetry hypertable."""
    return "CREATE EXTENSION IF NOT EXISTS timescaledb;"


def create_assets_table_ddl() -> str:
    """DDL for the spatial asset table (PRD Step 4/6 — pre-positioned resources).

    A generated ``geom`` POINT column lets PostGIS answer KNN ``<->`` / ST_Distance
    nearest queries; ``lat``/``lon`` are retained so the row maps 1:1 onto
    :class:`~disastermind.models.domain.Asset`.
    """
    return (
        f"CREATE TABLE IF NOT EXISTS {ASSETS_TABLE} (\n"
        "    asset_id   TEXT PRIMARY KEY,\n"
        "    type       TEXT NOT NULL,\n"
        "    lat        DOUBLE PRECISION NOT NULL,\n"
        "    lon        DOUBLE PRECISION NOT NULL,\n"
        "    capacity   INTEGER NOT NULL DEFAULT 0,\n"
        "    available  BOOLEAN NOT NULL DEFAULT TRUE,\n"
        "    fuel_pct   DOUBLE PRECISION NOT NULL DEFAULT 100.0,\n"
        f"    geom       GEOMETRY(Point, {SRID})\n"
        ");"
    )


def create_assets_geom_index_ddl() -> str:
    """GiST index backing nearest-asset spatial queries (PRD Step 6)."""
    return (
        f"CREATE INDEX IF NOT EXISTS {ASSETS_TABLE}_geom_idx "
        f"ON {ASSETS_TABLE} USING GIST (geom);"
    )


def create_zones_table_ddl() -> str:
    """DDL for population/vulnerability zones (PRD Step 4 equity weighting)."""
    return (
        f"CREATE TABLE IF NOT EXISTS {ZONES_TABLE} (\n"
        "    cell_id                      TEXT PRIMARY KEY,\n"
        "    lat                          DOUBLE PRECISION NOT NULL,\n"
        "    lon                          DOUBLE PRECISION NOT NULL,\n"
        "    population                   INTEGER NOT NULL DEFAULT 0,\n"
        "    elderly_density              DOUBLE PRECISION NOT NULL DEFAULT 0.0,\n"
        "    hospital_proximity           DOUBLE PRECISION NOT NULL DEFAULT 0.0,\n"
        "    road_accessibility           DOUBLE PRECISION NOT NULL DEFAULT 1.0,\n"
        "    informal_settlement_density  DOUBLE PRECISION NOT NULL DEFAULT 0.0,\n"
        "    mobility_impaired            INTEGER NOT NULL DEFAULT 0,\n"
        "    children                     INTEGER NOT NULL DEFAULT 0,\n"
        "    hospitalised                 INTEGER NOT NULL DEFAULT 0,\n"
        f"    geom                         GEOMETRY(Point, {SRID})\n"
        ");"
    )


def create_zones_geom_index_ddl() -> str:
    """GiST index backing zones-within-radius queries (PRD Step 4)."""
    return (
        f"CREATE INDEX IF NOT EXISTS {ZONES_TABLE}_geom_idx "
        f"ON {ZONES_TABLE} USING GIST (geom);"
    )


def create_telemetry_table_ddl() -> str:
    """DDL for the raw telemetry table prior to hypertable conversion (PRD Step 6)."""
    return (
        f"CREATE TABLE IF NOT EXISTS {TELEMETRY_TABLE} (\n"
        "    sensor_id  TEXT NOT NULL,\n"
        "    metric     TEXT NOT NULL,\n"
        "    value      DOUBLE PRECISION NOT NULL,\n"
        "    ts         TIMESTAMPTZ NOT NULL,\n"
        "    meta       JSONB NOT NULL DEFAULT '{}'::jsonb\n"
        ");"
    )


def create_telemetry_hypertable_ddl() -> str:
    """Convert the telemetry table into a TimescaleDB hypertable on ``ts``."""
    return (
        f"SELECT create_hypertable('{TELEMETRY_TABLE}', 'ts', "
        "if_not_exists => TRUE);"
    )


def create_telemetry_index_ddl() -> str:
    """Composite index for ``(sensor_id, metric, ts)`` range scans (PRD Step 3)."""
    return (
        f"CREATE INDEX IF NOT EXISTS {TELEMETRY_TABLE}_sensor_metric_ts_idx "
        f"ON {TELEMETRY_TABLE} (sensor_id, metric, ts DESC);"
    )


def schema_ddl() -> list[str]:
    """The full ordered DDL program (extensions, tables, hypertable, indexes).

    Returns each statement separately so callers can execute them one-by-one;
    :func:`schema_sql` joins them for a ``schema.sql`` file.
    """
    return [
        create_postgis_extension_ddl(),
        create_timescale_extension_ddl(),
        create_assets_table_ddl(),
        create_assets_geom_index_ddl(),
        create_zones_table_ddl(),
        create_zones_geom_index_ddl(),
        create_telemetry_table_ddl(),
        create_telemetry_hypertable_ddl(),
        create_telemetry_index_ddl(),
    ]


def schema_sql() -> str:
    """The complete schema as one runnable ``.sql`` script (newline-separated)."""
    header = (
        "-- DisasterMind backend schema (PRD Step 9 persistence, Step 6 telemetry).\n"
        "-- Generated by disastermind.integrations.sql.schema_sql(); do not edit by hand.\n"
        "-- PostGIS resource store (dm_assets, dm_zones) + TimescaleDB hypertable "
        "(dm_telemetry).\n"
    )
    return header + "\n".join(stmt + "\n" for stmt in schema_ddl())


# ----------------------------------------------------------------------- assets
def upsert_asset_sql(asset: Any) -> tuple[str, tuple]:
    """Build an INSERT .. ON CONFLICT upsert for one asset.

    Accepts an :class:`~disastermind.models.domain.Asset` or a JSON dict (as it
    crosses the bus, PRD Step 9). Mirrors
    :meth:`PostgisResourceRepo._upsert_asset_pg` exactly.
    """
    aid, atype, lat, lon, capacity, available, fuel_pct = _asset_fields(asset)
    sql = (
        f"INSERT INTO {ASSETS_TABLE} "
        "(asset_id, type, lat, lon, capacity, available, fuel_pct, geom) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, "
        f"ST_SetSRID(ST_MakePoint(%s, %s), {SRID})) "
        "ON CONFLICT (asset_id) DO UPDATE SET "
        "type=EXCLUDED.type, lat=EXCLUDED.lat, lon=EXCLUDED.lon, "
        "capacity=EXCLUDED.capacity, available=EXCLUDED.available, "
        "fuel_pct=EXCLUDED.fuel_pct, geom=EXCLUDED.geom"
    )
    # geom uses (lon, lat) order for ST_MakePoint.
    params = (aid, atype, lat, lon, capacity, available, fuel_pct, lon, lat)
    return sql, params


def get_asset_sql(asset_id: str) -> tuple[str, tuple]:
    """SELECT one asset by id."""
    cols = ", ".join(ASSET_COLUMNS)
    return f"SELECT {cols} FROM {ASSETS_TABLE} WHERE asset_id=%s", (asset_id,)


def all_assets_sql() -> tuple[str, tuple]:
    """SELECT all assets."""
    cols = ", ".join(ASSET_COLUMNS)
    return f"SELECT {cols} FROM {ASSETS_TABLE}", ()


def set_asset_available_sql(asset_id: str, available: bool) -> tuple[str, tuple]:
    """Flip an asset's availability after dispatch (PRD Step 6)."""
    return (
        f"UPDATE {ASSETS_TABLE} SET available=%s WHERE asset_id=%s",
        (bool(available), asset_id),
    )


def nearest_assets_sql(
    lat: float,
    lon: float,
    *,
    asset_type: Any | None = None,
    available_only: bool = True,
    max_distance_m: float | None = None,
    k: int = 5,
) -> tuple[str, tuple]:
    """KNN nearest-asset query using PostGIS ``<->`` ordering (PRD Step 6).

    Mirrors the in-memory haversine ranking in
    :meth:`PostgisResourceRepo.nearest_assets` (same filters, same ordering).
    ``ST_DistanceSphere`` yields metres so ``max_distance_m`` is directly usable.
    """
    cols = ", ".join(ASSET_COLUMNS)
    point = f"ST_SetSRID(ST_MakePoint(%s, %s), {SRID})"
    clauses: list[str] = []
    params: list[Any] = [lon, lat]  # for the distance SELECT expression
    if available_only:
        clauses.append("available = TRUE")
    if asset_type is not None:
        clauses.append("type = %s")
        params.append(_enum_value(asset_type))
    if max_distance_m is not None:
        clauses.append(f"ST_DistanceSphere(geom, {point}) <= %s")
        params.extend([lon, lat, float(max_distance_m)])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    # geom <-> point gives index-assisted nearest ordering.
    sql = (
        f"SELECT {cols}, ST_DistanceSphere(geom, {point}) AS distance_m "
        f"FROM {ASSETS_TABLE}{where} "
        f"ORDER BY geom <-> {point} LIMIT %s"
    )
    # distance SELECT (lon,lat) already at head of params; append ORDER BY point + limit.
    params.extend([lon, lat, int(max(0, k))])
    return sql, tuple(params)


# ------------------------------------------------------------------------- zones
def upsert_zone_sql(zone: Any) -> tuple[str, tuple]:
    """Build an INSERT .. ON CONFLICT upsert for one population/vulnerability zone.

    Persists every :class:`~disastermind.models.domain.VulnerabilityProfile`
    input (PRD Step 4) alongside the centroid + geometry, unlike the trimmed
    inline SQL in storage (which only stored population) — this is the richer
    form storage could adopt.
    """
    cell_id, lat, lon, population, vuln = _zone_fields(zone)
    scalar = ", ".join(ZONE_SCALAR_COLUMNS)
    vuln_cols = ", ".join(ZONE_VULN_COLUMNS)
    n_vals = len(ZONE_SCALAR_COLUMNS) + len(ZONE_VULN_COLUMNS)
    placeholders = ", ".join(["%s"] * n_vals)
    update_cols = ZONE_SCALAR_COLUMNS[1:] + ZONE_VULN_COLUMNS  # all but the PK
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols) + ", geom=EXCLUDED.geom"
    sql = (
        f"INSERT INTO {ZONES_TABLE} ({scalar}, {vuln_cols}, geom) "
        f"VALUES ({placeholders}, ST_SetSRID(ST_MakePoint(%s, %s), {SRID})) "
        f"ON CONFLICT (cell_id) DO UPDATE SET {updates}"
    )
    params = (
        cell_id,
        lat,
        lon,
        population,
        vuln["elderly_density"],
        vuln["hospital_proximity"],
        vuln["road_accessibility"],
        vuln["informal_settlement_density"],
        vuln["mobility_impaired"],
        vuln["children"],
        vuln["hospitalised"],
        lon,
        lat,
    )
    return sql, params


def get_zone_sql(cell_id: str) -> tuple[str, tuple]:
    """SELECT one zone by cell id."""
    cols = ", ".join(ZONE_SCALAR_COLUMNS + ZONE_VULN_COLUMNS)
    return f"SELECT {cols} FROM {ZONES_TABLE} WHERE cell_id=%s", (cell_id,)


def all_zones_sql() -> tuple[str, tuple]:
    """SELECT all zones."""
    cols = ", ".join(ZONE_SCALAR_COLUMNS + ZONE_VULN_COLUMNS)
    return f"SELECT {cols} FROM {ZONES_TABLE}", ()


def zones_within_sql(lat: float, lon: float, radius_m: float) -> tuple[str, tuple]:
    """Zones whose centroid lies within ``radius_m`` of the point (PRD Step 4)."""
    cols = ", ".join(ZONE_SCALAR_COLUMNS + ZONE_VULN_COLUMNS)
    point = f"ST_SetSRID(ST_MakePoint(%s, %s), {SRID})"
    sql = (
        f"SELECT {cols}, ST_DistanceSphere(geom, {point}) AS distance_m "
        f"FROM {ZONES_TABLE} "
        f"WHERE ST_DistanceSphere(geom, {point}) <= %s "
        f"ORDER BY distance_m"
    )
    return sql, (lon, lat, lon, lat, float(radius_m))


# --------------------------------------------------------------------- telemetry
def insert_telemetry_sql(point: Any) -> tuple[str, tuple]:
    """INSERT one telemetry frame into the hypertable (PRD Step 6).

    Mirrors :meth:`TimescaleTelemetryRepo._append_pg`. ``meta`` is JSON-encoded.
    """
    import json

    sensor_id, metric, value, ts, meta = _telemetry_fields(point)
    cols = ", ".join(TELEMETRY_COLUMNS)
    sql = (
        f"INSERT INTO {TELEMETRY_TABLE} ({cols}) VALUES (%s, %s, %s, %s, %s)"
    )
    return sql, (sensor_id, metric, value, ts, json.dumps(meta))


def query_telemetry_range_sql(
    sensor_id: str | None = None,
    metric: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> tuple[str, tuple]:
    """Time-range telemetry query (PRD Step 3 model-input assembly).

    Mirrors :meth:`TimescaleTelemetryRepo._query_range_pg` (inclusive ``ts``
    bounds, ordered ascending).
    """
    cols = ", ".join(TELEMETRY_COLUMNS)
    clauses: list[str] = []
    params: list[Any] = []
    if sensor_id is not None:
        clauses.append("sensor_id=%s")
        params.append(sensor_id)
    if metric is not None:
        clauses.append("metric=%s")
        params.append(metric)
    if start is not None:
        clauses.append("ts >= %s")
        params.append(start)
    if end is not None:
        clauses.append("ts <= %s")
        params.append(end)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT {cols} FROM {TELEMETRY_TABLE}{where} ORDER BY ts"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    return sql, tuple(params)


def latest_telemetry_sql(sensor_id: str, metric: str | None = None) -> tuple[str, tuple]:
    """Most-recent reading for a sensor (PRD Step 3)."""
    cols = ", ".join(TELEMETRY_COLUMNS)
    clauses = ["sensor_id=%s"]
    params: list[Any] = [sensor_id]
    if metric is not None:
        clauses.append("metric=%s")
        params.append(metric)
    sql = (
        f"SELECT {cols} FROM {TELEMETRY_TABLE} WHERE "
        + " AND ".join(clauses)
        + " ORDER BY ts DESC LIMIT 1"
    )
    return sql, tuple(params)


# ---------------------------------------------------------------- field coercion
def _enum_value(obj: Any) -> Any:
    """Return ``obj.value`` for enum members, else ``obj`` (accepts plain strings)."""
    return getattr(obj, "value", obj)


def _latlon(obj: Any) -> tuple[float, float]:
    """Coerce a LatLon / dict / pair into ``(lat, lon)`` floats — no storage import."""
    lat = getattr(obj, "lat", None)
    lon = getattr(obj, "lon", None)
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    if isinstance(obj, dict):
        return float(obj.get("lat", 0.0)), float(obj.get("lon", 0.0))
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        return float(obj[0]), float(obj[1])
    raise TypeError(f"cannot coerce {obj!r} to (lat, lon)")


def _asset_fields(asset: Any) -> tuple:
    """Extract the ordered asset column values from a dataclass or dict."""
    if isinstance(asset, dict):
        lat, lon = _latlon(asset["location"])
        return (
            asset["asset_id"],
            _enum_value(asset["type"]),
            lat,
            lon,
            int(asset.get("capacity", 0)),
            bool(asset.get("available", True)),
            float(asset.get("fuel_pct", 100.0)),
        )
    lat, lon = _latlon(asset.location)
    return (
        asset.asset_id,
        _enum_value(asset.type),
        lat,
        lon,
        int(asset.capacity),
        bool(asset.available),
        float(asset.fuel_pct),
    )


def _zone_fields(zone: Any) -> tuple:
    """Extract ``(cell_id, lat, lon, population, vuln_dict)`` from dataclass/dict."""
    defaults = {
        "elderly_density": 0.0,
        "hospital_proximity": 0.0,
        "road_accessibility": 1.0,
        "informal_settlement_density": 0.0,
        "mobility_impaired": 0,
        "children": 0,
        "hospitalised": 0,
    }
    if isinstance(zone, dict):
        lat, lon = _latlon(zone["centroid"])
        raw = zone.get("vulnerability") or {}
        if not isinstance(raw, dict):
            raw = {k: getattr(raw, k, defaults[k]) for k in defaults}
        vuln = {k: raw.get(k, defaults[k]) for k in defaults}
        return (
            zone["cell_id"],
            lat,
            lon,
            int(zone.get("population", 0)),
            _normalise_vuln(vuln),
        )
    lat, lon = _latlon(zone.centroid)
    vp = getattr(zone, "vulnerability", None)
    vuln = {k: getattr(vp, k, defaults[k]) for k in defaults} if vp is not None else dict(defaults)
    return (zone.cell_id, lat, lon, int(zone.population), _normalise_vuln(vuln))


def _normalise_vuln(vuln: dict) -> dict:
    """Coerce vulnerability inputs to the right numeric types."""
    return {
        "elderly_density": float(vuln["elderly_density"]),
        "hospital_proximity": float(vuln["hospital_proximity"]),
        "road_accessibility": float(vuln["road_accessibility"]),
        "informal_settlement_density": float(vuln["informal_settlement_density"]),
        "mobility_impaired": int(vuln["mobility_impaired"]),
        "children": int(vuln["children"]),
        "hospitalised": int(vuln["hospitalised"]),
    }


def _telemetry_fields(point: Any) -> tuple:
    """Extract ``(sensor_id, metric, value, ts, meta)`` from dataclass/dict."""
    if isinstance(point, dict):
        return (
            str(point["sensor_id"]),
            str(point["metric"]),
            float(point["value"]),
            str(point["ts"]),
            dict(point.get("meta") or {}),
        )
    return (
        str(point.sensor_id),
        str(point.metric),
        float(point.value),
        str(point.ts),
        dict(getattr(point, "meta", {}) or {}),
    )
