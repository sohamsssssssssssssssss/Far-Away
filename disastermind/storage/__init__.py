"""Persistence layer (PRD Step 9 storage).

Four repositories, each with a stdlib/in-memory fallback so the system runs with
NO external service (graceful degradation, PRD Step 10):

  * :class:`PostgisResourceRepo`     — spatial asset/zone state (PostgreSQL+PostGIS)
  * :class:`TimescaleTelemetryRepo`  — sensor time-series (TimescaleDB)
  * :class:`ElasticsearchAuditRepo`  — decision-audit index/search (Elasticsearch)
  * :class:`MinioArtifactStore`      — imagery / model artefacts (MinIO)

The :class:`Storage` facade groups all four. It is **offline by default**: a
backend is only contacted when ``live=True`` is requested, so importing and
constructing storage never touches the network during tests.
"""
from __future__ import annotations

from dataclasses import dataclass

from .elasticsearch_audit_repo import ElasticsearchAuditRepo
from .minio_artifact_store import MinioArtifactStore
from .postgis_resource_repo import PostgisResourceRepo
from .timescale_telemetry_repo import TelemetryPoint, TimescaleTelemetryRepo

__all__ = [
    "PostgisResourceRepo",
    "TimescaleTelemetryRepo",
    "TelemetryPoint",
    "ElasticsearchAuditRepo",
    "MinioArtifactStore",
    "Storage",
]


@dataclass
class Storage:
    """Aggregate handle for all four repositories."""

    resources: PostgisResourceRepo
    telemetry: TimescaleTelemetryRepo
    audit: ElasticsearchAuditRepo
    artifacts: MinioArtifactStore

    @classmethod
    def in_memory(cls) -> Storage:
        """All repos in fallback mode — zero external services (default for tests)."""
        return cls(
            resources=PostgisResourceRepo(),
            telemetry=TimescaleTelemetryRepo(),
            audit=ElasticsearchAuditRepo(),
            artifacts=MinioArtifactStore(),
        )

    @classmethod
    def from_settings(cls, settings=None, *, live: bool = False) -> Storage:
        """Build from :class:`~disastermind.core.config.Settings`.

        ``live=False`` (default) yields the in-memory fallback so nothing connects.
        ``live=True`` wires the real backends from the configured DSNs/URLs — each
        repo still degrades to fallback on its own if its server is unreachable.
        """
        if not live:
            return cls.in_memory()
        from ..core.config import settings as default_settings

        s = settings or default_settings
        return cls(
            resources=PostgisResourceRepo(dsn=s.postgres_dsn),
            telemetry=TimescaleTelemetryRepo(dsn=s.timescale_dsn),
            audit=ElasticsearchAuditRepo(url=s.elasticsearch_url),
            artifacts=MinioArtifactStore(),
        )

    @property
    def all_fallback(self) -> bool:
        """True when every repository is running in degraded/in-memory mode."""
        return (
            self.resources.is_fallback
            and self.telemetry.is_fallback
            and self.audit.is_fallback
            and self.artifacts.is_fallback
        )
