"""Spatial asset/zone state repository (PRD Step 9 — PostGIS persistence).

Stores pre-positioned :class:`~disastermind.models.domain.Asset` rows and
:class:`~disastermind.models.domain.PopulationCell` zones with a geometry
column, and answers *nearest* spatial queries (the resource/field tiers ask
"which available boat is closest to this drowning cell?", PRD Step 4 / 6).

Backend selection (see :class:`~disastermind.storage.facade.Storage`):
  * **PostGIS** when a real DSN is reachable — psycopg is imported *lazily*
    inside :meth:`_connect`, wrapped in try/except.
  * **Fallback** an in-memory dict keyed by id; nearest queries use
    :func:`disastermind.models.geo.haversine` so the system works fully offline
    (PRD Step 10 graceful degradation). No network at import or in any test.
"""
from __future__ import annotations

import logging
from typing import Any

from ..models.domain import Asset, AssetType, PopulationCell, VulnerabilityProfile
from ..models.geo import LatLon, haversine
from ._common import coerce_latlon

log = logging.getLogger("disastermind.storage.postgis")


class PostgisResourceRepo:
    """Spatial repo for assets and population/vulnerability zones.

    Pass a non-empty ``dsn`` to attempt a PostGIS connection; on any failure
    (driver missing, server unreachable) the repo silently degrades to the
    in-memory fallback so callers need no error handling.
    """

    def __init__(self, dsn: str = "") -> None:
        self.dsn = dsn
        self._assets: dict[str, Asset] = {}
        self._zones: dict[str, PopulationCell] = {}
        self._conn = self._connect(dsn) if dsn else None

    # ------------------------------------------------------------- backend wiring
    @property
    def is_fallback(self) -> bool:
        """True when running on the in-memory fallback (no live PostGIS)."""
        return self._conn is None

    def _connect(self, dsn: str):  # pragma: no cover - optional dependency/network
        try:
            import psycopg  # type: ignore

            conn = psycopg.connect(dsn, connect_timeout=2)
            return conn
        except Exception:
            log.warning("psycopg/PostGIS unavailable; in-memory spatial fallback")
            return None

    # ----------------------------------------------------------------- coercion
    @staticmethod
    def _as_asset(obj: Any) -> Asset:
        if isinstance(obj, Asset):
            return obj
        if isinstance(obj, dict):
            atype = obj["type"]
            return Asset(
                asset_id=obj["asset_id"],
                type=atype if isinstance(atype, AssetType) else AssetType(atype),
                location=coerce_latlon(obj["location"]),
                capacity=int(obj.get("capacity", 0)),
                available=bool(obj.get("available", True)),
                fuel_pct=float(obj.get("fuel_pct", 100.0)),
            )
        raise TypeError(f"cannot coerce {obj!r} to Asset")

    @staticmethod
    def _as_zone(obj: Any) -> PopulationCell:
        if isinstance(obj, PopulationCell):
            return obj
        if isinstance(obj, dict):
            vuln = obj.get("vulnerability") or {}
            if isinstance(vuln, VulnerabilityProfile):
                profile = vuln
            else:
                profile = VulnerabilityProfile(
                    **{
                        k: v
                        for k, v in vuln.items()
                        if k in VulnerabilityProfile.__dataclass_fields__
                    }
                )
            return PopulationCell(
                cell_id=obj["cell_id"],
                centroid=coerce_latlon(obj["centroid"]),
                population=int(obj.get("population", 0)),
                vulnerability=profile,
            )
        raise TypeError(f"cannot coerce {obj!r} to PopulationCell")

    # -------------------------------------------------------------------- assets
    def upsert_asset(self, asset: Any) -> Asset:
        """Insert/replace one asset's spatial state."""
        a = self._as_asset(asset)
        if self._conn is None:
            self._assets[a.asset_id] = a
            return a
        return self._upsert_asset_pg(a)  # pragma: no cover

    def upsert_assets(self, assets: list[Any]) -> int:
        for a in assets:
            self.upsert_asset(a)
        return len(assets)

    def get_asset(self, asset_id: str) -> Asset | None:
        if self._conn is None:
            return self._assets.get(asset_id)
        return self._get_asset_pg(asset_id)  # pragma: no cover

    def all_assets(self) -> list[Asset]:
        if self._conn is None:
            return list(self._assets.values())
        return self._all_assets_pg()  # pragma: no cover

    def set_available(self, asset_id: str, available: bool) -> bool:
        """Flip an asset's availability (after dispatch, PRD Step 6)."""
        a = self.get_asset(asset_id)
        if a is None:
            return False
        a.available = available
        self.upsert_asset(a)
        return True

    # --------------------------------------------------------------------- zones
    def upsert_zone(self, zone: Any) -> PopulationCell:
        z = self._as_zone(zone)
        if self._conn is None:
            self._zones[z.cell_id] = z
            return z
        return self._upsert_zone_pg(z)  # pragma: no cover

    def upsert_zones(self, zones: list[Any]) -> int:
        for z in zones:
            self.upsert_zone(z)
        return len(zones)

    def get_zone(self, cell_id: str) -> PopulationCell | None:
        if self._conn is None:
            return self._zones.get(cell_id)
        return self._get_zone_pg(cell_id)  # pragma: no cover

    def all_zones(self) -> list[PopulationCell]:
        if self._conn is None:
            return list(self._zones.values())
        return self._all_zones_pg()  # pragma: no cover

    # ----------------------------------------------------------- spatial queries
    def nearest_asset(
        self,
        point: Any,
        *,
        asset_type: AssetType | str | None = None,
        available_only: bool = True,
        max_distance_m: float | None = None,
    ) -> tuple[Asset, float] | None:
        """Return ``(asset, distance_m)`` of the closest matching asset, or None.

        Fallback uses :func:`disastermind.models.geo.haversine`; the PostGIS
        backend would use ``ST_Distance`` / KNN ``<->`` (same ordering).
        """
        ranked = self.nearest_assets(
            point,
            k=1,
            asset_type=asset_type,
            available_only=available_only,
            max_distance_m=max_distance_m,
        )
        return ranked[0] if ranked else None

    def nearest_assets(
        self,
        point: Any,
        k: int = 5,
        *,
        asset_type: AssetType | str | None = None,
        available_only: bool = True,
        max_distance_m: float | None = None,
    ) -> list[tuple[Asset, float]]:
        """Return the ``k`` nearest matching assets as ``(asset, distance_m)``."""
        origin = coerce_latlon(point)
        want = (
            asset_type
            if asset_type is None or isinstance(asset_type, AssetType)
            else AssetType(asset_type)
        )
        scored: list[tuple[Asset, float]] = []
        for a in self.all_assets():
            if available_only and not a.available:
                continue
            if want is not None and a.type is not want:
                continue
            d = haversine(origin, a.location)
            if max_distance_m is not None and d > max_distance_m:
                continue
            scored.append((a, d))
        scored.sort(key=lambda t: t[1])
        return scored[: max(0, k)]

    def zones_within(self, point: Any, radius_m: float) -> list[tuple[PopulationCell, float]]:
        """All zones whose centroid lies within ``radius_m`` of ``point``."""
        origin = coerce_latlon(point)
        out: list[tuple[PopulationCell, float]] = []
        for z in self.all_zones():
            d = haversine(origin, z.centroid)
            if d <= radius_m:
                out.append((z, d))
        out.sort(key=lambda t: t[1])
        return out

    def nearest_zone(self, point: Any) -> tuple[PopulationCell, float] | None:
        origin = coerce_latlon(point)
        best: tuple[PopulationCell, float] | None = None
        for z in self.all_zones():
            d = haversine(origin, z.centroid)
            if best is None or d < best[1]:
                best = (z, d)
        return best

    def close(self) -> None:
        if self._conn is not None:  # pragma: no cover
            try:
                self._conn.close()
            except Exception:
                log.exception("error closing PostGIS connection")
            self._conn = None

    # ---------------------------------------------------- PostGIS impls (lazy) --
    # These run only against a live server and are excluded from the offline
    # test path. The SQL is now sourced from the single canonical builder module
    # :mod:`disastermind.integrations.sql` (imported lazily, no import-time/network
    # dependency) so DDL and statements live in exactly one place; semantics still
    # mirror the in-memory fallback above.
    @staticmethod
    def _sql():  # pragma: no cover - imported only on the live backend path
        from ..integrations import sql as _sql

        return _sql

    def _upsert_asset_pg(self, a: Asset) -> Asset:  # pragma: no cover
        stmt, params = self._sql().upsert_asset_sql(a)
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            self._conn.commit()
        return a

    def _get_asset_pg(self, asset_id: str) -> Asset | None:  # pragma: no cover
        stmt, params = self._sql().get_asset_sql(asset_id)
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            row = cur.fetchone()
        return self._row_to_asset(row) if row else None

    def _all_assets_pg(self) -> list[Asset]:  # pragma: no cover
        stmt, params = self._sql().all_assets_sql()
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall()
        return [self._row_to_asset(r) for r in rows]

    @staticmethod
    def _row_to_asset(row) -> Asset:  # pragma: no cover
        return Asset(
            asset_id=row[0],
            type=AssetType(row[1]),
            location=LatLon(float(row[2]), float(row[3])),
            capacity=int(row[4]),
            available=bool(row[5]),
            fuel_pct=float(row[6]),
        )

    def _upsert_zone_pg(self, z: PopulationCell) -> PopulationCell:  # pragma: no cover
        stmt, params = self._sql().upsert_zone_sql(z)
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            self._conn.commit()
        return z

    def _get_zone_pg(self, cell_id: str) -> PopulationCell | None:  # pragma: no cover
        stmt, params = self._sql().get_zone_sql(cell_id)
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            row = cur.fetchone()
        if not row:
            return None
        return PopulationCell(row[0], LatLon(float(row[1]), float(row[2])), int(row[3]))

    def _all_zones_pg(self) -> list[PopulationCell]:  # pragma: no cover
        stmt, params = self._sql().all_zones_sql()
        with self._conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall()
        return [
            PopulationCell(r[0], LatLon(float(r[1]), float(r[2])), int(r[3])) for r in rows
        ]
