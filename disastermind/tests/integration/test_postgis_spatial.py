"""Live PostGIS spatial round-trip (docker-compose `postgis` service).

Gated by tests/integration/conftest.py (collected only when DM_INTEGRATION=1).
Self-skips when psycopg is missing or PostGIS is unreachable. Provisions the
schema via the repo's own DDL builders (`disastermind.integrations.sql`) so the
test is consistent with the live SQL path, then exercises upsert + nearest /
within spatial queries through `PostgisResourceRepo`.
"""
from __future__ import annotations

import socket
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from disastermind.integrations import sql  # noqa: E402
from disastermind.models.domain import Asset, AssetType, PopulationCell  # noqa: E402
from disastermind.models.geo import LatLon  # noqa: E402
from disastermind.storage.postgis_resource_repo import PostgisResourceRepo  # noqa: E402

PG_HOST = "localhost"
PG_PORT = 5432
DSN = f"postgresql://disastermind:disastermind@{PG_HOST}:{PG_PORT}/disastermind"


def _reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(PG_HOST, PG_PORT),
    reason=f"PostGIS unreachable at {PG_HOST}:{PG_PORT} (start `docker compose up -d postgis`)",
)


def _ensure_schema() -> None:
    """Create the PostGIS extension + asset/zone tables (idempotent)."""
    ddls = [
        sql.create_postgis_extension_ddl(),
        sql.create_assets_table_ddl(),
        sql.create_assets_geom_index_ddl(),
        sql.create_zones_table_ddl(),
        sql.create_zones_geom_index_ddl(),
    ]
    with psycopg.connect(DSN, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            for stmt in ddls:
                cur.execute(stmt)
        conn.commit()


def test_postgis_nearest_and_within():
    _ensure_schema()
    repo = PostgisResourceRepo(dsn=DSN)
    if repo.is_fallback:
        pytest.skip("psycopg present but PostGIS connection failed")

    tag = uuid.uuid4().hex[:8]
    mumbai = LatLon(19.0760, 72.8777)
    near = Asset(
        asset_id=f"boat-near-{tag}", type=AssetType.BOAT,
        location=LatLon(19.080, 72.880), capacity=12,
    )
    far = Asset(
        asset_id=f"boat-far-{tag}", type=AssetType.BOAT,
        location=LatLon(18.520, 73.850), capacity=12,  # ~120 km (Pune)
    )
    heli = Asset(
        asset_id=f"heli-{tag}", type=AssetType.HELICOPTER,
        location=LatLon(19.100, 72.900), capacity=6,
    )
    zone = PopulationCell(
        cell_id=f"zone-{tag}", centroid=LatLon(19.070, 72.880), population=5000,
    )

    try:
        assert repo.upsert_assets([near, far, heli]) == 3
        repo.upsert_zone(zone)

        # The asset rows we wrote are retrievable from live PostGIS.
        ids = {a.asset_id for a in repo.all_assets()}
        assert {near.asset_id, far.asset_id, heli.asset_id} <= ids

        # Nearest BOAT to Mumbai exists and reports a sane distance.
        result = repo.nearest_asset(mumbai, asset_type=AssetType.BOAT)
        assert result is not None
        _, dist = result
        assert dist >= 0.0

        # Among OUR boats, 'near' ranks before 'far' (robust to other rows).
        ranked = repo.nearest_assets(mumbai, k=1000, asset_type=AssetType.BOAT)
        mine = [a.asset_id for a, _ in ranked if a.asset_id in {near.asset_id, far.asset_id}]
        assert mine == [near.asset_id, far.asset_id]

        # Type filter excludes the helicopter from a BOAT query.
        assert heli.asset_id not in {a.asset_id for a, _ in ranked}

        # Our zone is within 5 km of the Mumbai centroid.
        within = repo.zones_within(mumbai, radius_m=5000)
        assert any(z.cell_id == zone.cell_id for z, _ in within)
    finally:
        repo.close()
