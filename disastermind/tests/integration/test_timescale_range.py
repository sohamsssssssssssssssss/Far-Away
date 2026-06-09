"""Live TimescaleDB range-query round-trip (docker-compose `timescaledb` service).

Gated by tests/integration/conftest.py (collected only when DM_INTEGRATION=1).
Self-skips when psycopg is missing or TimescaleDB is unreachable. Provisions the
telemetry hypertable via the repo's own DDL builders, appends readings through
`TimescaleTelemetryRepo`, and asserts the time-range query returns them.
"""
from __future__ import annotations

import socket
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from disastermind.integrations import sql  # noqa: E402
from disastermind.storage.timescale_telemetry_repo import (  # noqa: E402
    TelemetryPoint,
    TimescaleTelemetryRepo,
)

TS_HOST = "localhost"
TS_PORT = 5433
DSN = f"postgresql://disastermind:disastermind@{TS_HOST}:{TS_PORT}/dm_telemetry"


def _reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(TS_HOST, TS_PORT),
    reason=f"TimescaleDB unreachable at {TS_HOST}:{TS_PORT} (start `docker compose up -d timescaledb`)",
)


def _ensure_schema() -> None:
    """Create the timescaledb extension + telemetry hypertable (idempotent)."""
    ddls = [
        sql.create_timescale_extension_ddl(),
        sql.create_telemetry_table_ddl(),
        sql.create_telemetry_hypertable_ddl(),
        sql.create_telemetry_index_ddl(),
    ]
    with psycopg.connect(DSN, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            for stmt in ddls:
                cur.execute(stmt)
        conn.commit()


def test_timescale_append_and_range():
    _ensure_schema()
    repo = TimescaleTelemetryRepo(dsn=DSN)
    if repo.is_fallback:
        pytest.skip("psycopg present but TimescaleDB connection failed")

    sensor = f"river-gauge-{uuid.uuid4().hex[:8]}"
    points = [
        TelemetryPoint(sensor_id=sensor, metric="river_level_m", value=4.1,
                       ts="2026-06-08T10:00:00+00:00"),
        TelemetryPoint(sensor_id=sensor, metric="river_level_m", value=4.6,
                       ts="2026-06-08T10:15:00+00:00"),
        TelemetryPoint(sensor_id=sensor, metric="river_level_m", value=5.2,
                       ts="2026-06-08T10:30:00+00:00"),
        # Outside the query window below — must be excluded.
        TelemetryPoint(sensor_id=sensor, metric="river_level_m", value=9.9,
                       ts="2026-06-08T12:00:00+00:00"),
    ]

    assert repo.append_many(points) == len(points)

    rows = repo.query_range(
        sensor_id=sensor,
        metric="river_level_m",
        start="2026-06-08T10:00:00+00:00",
        end="2026-06-08T10:45:00+00:00",
    )
    assert len(rows) == 3
    values = sorted(p.value for p in rows)
    assert values == [4.1, 4.6, 5.2]
    assert all(r.sensor_id == sensor for r in rows)
    repo.close()
