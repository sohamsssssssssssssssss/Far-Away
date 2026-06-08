"""Object store for imagery & model artefacts (PRD Step 9 — MinIO/S3).

Stores binary artefacts: satellite/drone imagery used by the prediction tier and
serialised model weights/SHAP bundles (PRD Step 3/9). Objects are addressed by a
flat ``key`` within a bucket.

Backend selection (see :class:`~disastermind.storage.facade.Storage`):
  * **MinIO/S3** when an endpoint is configured — the ``minio`` client is
    imported *lazily* inside :meth:`_connect`, wrapped in try/except.
  * **Fallback** a local temp directory on disk; keys map to files (slashes are
    encoded so nested keys stay flat), so artefact put/get works fully offline
    (PRD Step 10). No network at import or in any test path.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Iterator

log = logging.getLogger("disastermind.storage.minio")


class MinioArtifactStore:
    """put/get binary artefacts with a local-temp-dir fallback.

    Pass ``endpoint``/``access_key``/``secret_key`` to attempt a MinIO
    connection; on any failure the store degrades to a local temp directory
    (``base_dir`` if given, else a fresh ``tempfile.mkdtemp``).
    """

    def __init__(
        self,
        endpoint: str = "",
        bucket: str = "disastermind-artifacts",
        access_key: str = "",
        secret_key: str = "",
        secure: bool = False,
        base_dir: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.bucket = bucket
        self._client = (
            self._connect(endpoint, access_key, secret_key, secure) if endpoint else None
        )
        if self._client is None:
            self._base_dir = base_dir or tempfile.mkdtemp(prefix="dm_artifacts_")
            os.makedirs(os.path.join(self._base_dir, bucket), exist_ok=True)
        else:  # pragma: no cover - requires live MinIO
            self._base_dir = ""

    @property
    def is_fallback(self) -> bool:
        return self._client is None

    @property
    def base_dir(self) -> str:
        """Local fallback root (empty string when backed by live MinIO)."""
        return self._base_dir

    def _connect(self, endpoint, access_key, secret_key, secure):  # pragma: no cover
        try:
            from minio import Minio  # type: ignore

            client = Minio(
                endpoint, access_key=access_key, secret_key=secret_key, secure=secure
            )
            if not client.bucket_exists(self.bucket):
                client.make_bucket(self.bucket)
            return client
        except Exception:
            log.warning("minio unavailable; local temp-dir artefact fallback")
            return None

    # ------------------------------------------------------------ key <-> path
    @staticmethod
    def _encode(key: str) -> str:
        # keep nested keys flat on the local fs; reversible & path-safe
        return key.replace("%", "%25").replace("/", "%2F")

    def _path(self, key: str) -> str:
        return os.path.join(self._base_dir, self.bucket, self._encode(key))

    # -------------------------------------------------------------- put / get
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store ``data`` under ``key``; returns the key."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("artefact data must be bytes")
        if self._client is None:
            with open(self._path(key), "wb") as fh:
                fh.write(data)
            return key
        return self._put_minio(key, bytes(data), content_type)  # pragma: no cover

    def get(self, key: str) -> bytes:
        """Fetch the artefact stored under ``key`` (raises KeyError if absent)."""
        if self._client is None:
            path = self._path(key)
            if not os.path.exists(path):
                raise KeyError(key)
            with open(path, "rb") as fh:
                return fh.read()
        return self._get_minio(key)  # pragma: no cover

    def exists(self, key: str) -> bool:
        if self._client is None:
            return os.path.exists(self._path(key))
        return self._exists_minio(key)  # pragma: no cover

    def delete(self, key: str) -> bool:
        if self._client is None:
            path = self._path(key)
            if os.path.exists(path):
                os.remove(path)
                return True
            return False
        return self._delete_minio(key)  # pragma: no cover

    def list_keys(self, prefix: str = "") -> Iterator[str]:
        """Iterate stored keys (optionally filtered by ``prefix``)."""
        if self._client is not None:  # pragma: no cover
            for obj in self._client.list_objects(self.bucket, prefix=prefix, recursive=True):
                yield obj.object_name
            return
        root = os.path.join(self._base_dir, self.bucket)
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root)):
            key = self._decode(name)
            if key.startswith(prefix):
                yield key

    @staticmethod
    def _decode(name: str) -> str:
        return name.replace("%2F", "/").replace("%25", "%")

    # ----------------------------------------------------- MinIO impls (lazy) --
    def _put_minio(self, key, data, content_type):  # pragma: no cover
        import io

        self._client.put_object(
            self.bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )
        return key

    def _get_minio(self, key):  # pragma: no cover
        resp = self._client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def _exists_minio(self, key):  # pragma: no cover
        try:
            self._client.stat_object(self.bucket, key)
            return True
        except Exception:
            return False

    def _delete_minio(self, key):  # pragma: no cover
        try:
            self._client.remove_object(self.bucket, key)
            return True
        except Exception:
            return False
