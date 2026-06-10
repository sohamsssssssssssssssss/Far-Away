"""Time-series sensor-telemetry repository (PRD Step 9 — TimescaleDB).

Appends IoT/sensor frames (river gauges, seismometers, GPS beacons — PRD Step 6)
and answers *time-range* queries used by the prediction tier to assemble model
inputs and by the audit/replay tooling.

Backend selection (see :class:`~disastermind.storage.facade.Storage`):
  * **TimescaleDB** when a real DSN is reachable — psycopg imported *lazily*
    inside :meth:`_connect`, wrapped in try/except.
  * **Fallback** an in-memory append-only list with binary-search range scans,
    so prediction works fully offline (PRD Step 10). No network at import time.

Timestamps are ISO-8601 UTC strings (matching
:func:`disastermind.core.contracts.utcnow_iso`); lexicographic ordering of these
strings is chronological, which the fallback range query relies on.
"""
from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass, field
from typing import Any

from ..core.contracts import utcnow_iso

log = logging.getLogger("disastermind.storage.timescale")


@dataclass(frozen=True)
class TelemetryPoint:
    """One sensor reading on the hypertable timeline (PRD Step 6)."""

    sensor_id: str
    metric: str  # e.g. "river_level_m", "pga_g", "gps_status"
    value: float
    ts: str = field(default_factory=utcnow_iso)  # ISO-8601 UTC
    meta: dict = field(default_factory=dict)


class TimescaleTelemetryRepo:
    """Append + time-range query of sensor telemetry.

    Pass a non-empty ``dsn`` to attempt a TimescaleDB connection; on any
    failure the repo degrades to the in-memory append-only fallback.
    """

    def __init__(self, dsn: str = "") -> None:
        self.dsn = dsn
        # kept sorted by ts (insertion is usually monotonic but we tolerate
        # late/out-of-order frames by re-sorting only when needed).
        self._points: list[TelemetryPoint] = []
        self._sorted = True
        self._conn = self._connect(dsn) if dsn else None

    @property
    def is_fallback(self) -> bool:
        return self._conn is None

    def _connect(self, dsn: str):  # pragma: no cover - optional dependency/network
        try:
            import psycopg  # type: ignore

            return psycopg.connect(dsn, connect_timeout=2)
        except Exception:
            log.warning("psycopg/TimescaleDB unavailable; in-memory telemetry fallback")
            return None

    # ----------------------------------------------------------------- coercion
    @staticmethod
    def _as_point(obj: Any) -> TelemetryPoint:
        if isinstance(obj, TelemetryPoint):
            return obj
        if isinstance(obj, dict):
            return TelemetryPoint(
                sensor_id=str(obj["sensor_id"]),
                metric=str(obj["metric"]),
                value=float(obj["value"]),
                ts=str(obj.get("ts") or utcnow_iso()),
                meta=dict(obj.get("meta") or {}),
            )
        raise TypeError(f"cannot coerce {obj!r} to TelemetryPoint")

    # -------------------------------------------------------------------- append
    def append(self, point: Any) -> TelemetryPoint:
        """Append a single reading to the hypertable."""
        p = self._as_point(point)
        if self._conn is None:
            if self._sorted and self._points and p.ts < self._points[-1].ts:
                self._sorted = False
            self._points.append(p)
            return p
        return self._append_pg(p)  # pragma: no cover

    def append_many(self, points: list[Any]) -> int:
        for p in points:
            self.append(p)
        return len(points)

    # --------------------------------------------------------------------- query
    def _ensure_sorted(self) -> None:
        if not self._sorted:
            self._points.sort(key=lambda p: p.ts)
            self._sorted = True

    def query_range(
        self,
        sensor_id: str | None = None,
        metric: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[TelemetryPoint]:
        """Return readings in ``[start, end]`` (inclusive), optionally filtered.

        ``start``/``end`` are ISO-8601 strings; ``None`` means unbounded.
        """
        if self._conn is not None:
            return self._query_range_pg(sensor_id, metric, start, end, limit)  # pragma: no cover
        self._ensure_sorted()
        keys = [p.ts for p in self._points]
        lo = bisect.bisect_left(keys, start) if start is not None else 0
        hi = bisect.bisect_right(keys, end) if end is not None else len(keys)
        out: list[TelemetryPoint] = []
        for p in self._points[lo:hi]:
            if sensor_id is not None and p.sensor_id != sensor_id:
                continue
            if metric is not None and p.metric != metric:
                continue
            out.append(p)
            if limit is not None and len(out) >= limit:
                break
        return out

    def latest(self, sensor_id: str, metric: str | None = None) -> TelemetryPoint | None:
        """Most-recent reading for a sensor (PRD Step 3 model-input assembly)."""
        if self._conn is not None:
            return self._latest_pg(sensor_id, metric)  # pragma: no cover
        self._ensure_sorted()
        for p in reversed(self._points):
            if p.sensor_id != sensor_id:
                continue
            if metric is not None and p.metric != metric:
                continue
            return p
        return None

    def count(self) -> int:
        if self._conn is not None:  # pragma: no cover
            with self._conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM dm_telemetry")
                return int(cur.fetchone()[0])
        return len(self._points)

    def close(self) -> None:
        if self._conn is not None:  # pragma: no cover
            try:
                self._conn.close()
            except Exception:
                log.exception("error closing TimescaleDB connection")
            self._conn = None

    # --------------------------------------------------- Timescale impls (lazy) -
    # SQL is sourced from the single canonical builder module
    # :mod:`disastermind.integrations.sql` (imported lazily — no import-time or
    # network dependency) so statements live in exactly one place; the queries
    # mirror the in-memory fallback semantics above.
    @staticmethod
    def _sql():  # pragma: no cover - imported only on the live backend path
        from ..integrations import sql as _sql

        return _sql

    def _append_pg(self, p: TelemetryPoint) -> TelemetryPoint:  # pragma: no cover
        stmt, params = self._sql().insert_telemetry_sql(p)
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            self._conn.commit()
        return p

    def _query_range_pg(self, sensor_id, metric, start, end, limit):  # pragma: no cover
        stmt, params = self._sql().query_telemetry_range_sql(
            sensor_id, metric, start, end, limit
        )
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    def _latest_pg(self, sensor_id, metric):  # pragma: no cover
        stmt, params = self._sql().latest_telemetry_sql(sensor_id, metric)
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            row = cur.fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row) -> TelemetryPoint:  # pragma: no cover
        import json

        meta = row[4]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        return TelemetryPoint(row[0], row[1], float(row[2]), str(row[3]), meta or {})
