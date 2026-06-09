"""Live MinIO put/get round-trip (docker-compose `minio` service).

Gated by tests/integration/conftest.py (collected only when DM_INTEGRATION=1).
Self-skips when the minio client is missing or MinIO is unreachable. Stores a
binary artefact and exercises get / exists / list_keys / delete through
`MinioArtifactStore`. Uses the default bucket with a unique key prefix so runs
don't collide, and cleans up after itself.
"""
from __future__ import annotations

import socket
import uuid

import pytest

pytest.importorskip("minio")

from disastermind.storage.minio_artifact_store import MinioArtifactStore  # noqa: E402

MINIO_HOST = "localhost"
MINIO_PORT = 9000
MINIO_ENDPOINT = f"{MINIO_HOST}:{MINIO_PORT}"


def _reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(MINIO_HOST, MINIO_PORT),
    reason=f"MinIO unreachable at {MINIO_ENDPOINT} (start `docker compose up -d minio`)",
)


def test_minio_put_get_delete():
    store = MinioArtifactStore(
        endpoint=MINIO_ENDPOINT,
        access_key="disastermind",
        secret_key="disastermind",
        secure=False,
    )
    if store.is_fallback:
        pytest.skip("minio client present but MinIO connection failed")

    tag = uuid.uuid4().hex[:10]
    key = f"dm-it-{tag}/shakemap.bin"
    data = b"\x89PNG\r\n\x1a\n" + b"synthetic-shakemap-raster" * 64

    try:
        assert store.put(key, data, content_type="image/png") == key
        assert store.exists(key) is True
        assert store.get(key) == data
        listed = list(store.list_keys(prefix=f"dm-it-{tag}/"))
        assert key in listed
    finally:
        deleted = store.delete(key)
        assert deleted is True
        assert store.exists(key) is False
