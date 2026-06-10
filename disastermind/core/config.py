"""Runtime configuration.

Dependency-light (stdlib only) so the package imports without pydantic/dotenv.
Values are read from the environment with sane defaults; see ``.env.example``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- coordination loop -------------------------------------------------
    loop_interval_seconds: int = field(default_factory=lambda: _env_int("DM_LOOP_INTERVAL", 30))
    escalation_timeout_seconds: int = field(
        default_factory=lambda: _env_int("DM_ESCALATION_TIMEOUT", 300)
    )
    grid_cell_meters: int = field(default_factory=lambda: _env_int("DM_GRID_METERS", 100))

    # --- message bus -------------------------------------------------------
    kafka_brokers: str = field(default_factory=lambda: _env("DM_KAFKA_BROKERS", ""))
    kafka_backup_brokers: str = field(default_factory=lambda: _env("DM_KAFKA_BACKUP", ""))
    use_kafka: bool = field(default_factory=lambda: _env_bool("DM_USE_KAFKA", False))

    # --- storage -----------------------------------------------------------
    postgres_dsn: str = field(
        default_factory=lambda: _env("DM_POSTGRES_DSN", "postgresql://localhost/disastermind")
    )
    timescale_dsn: str = field(
        default_factory=lambda: _env("DM_TIMESCALE_DSN", "postgresql://localhost/dm_telemetry")
    )
    elasticsearch_url: str = field(
        default_factory=lambda: _env("DM_ELASTICSEARCH_URL", "")
    )
    audit_log_path: str = field(default_factory=lambda: _env("DM_AUDIT_LOG", "./audit.jsonl"))

    # --- external feeds (PRD Step 2) --------------------------------------
    imd_base_url: str = field(default_factory=lambda: _env("DM_IMD_URL", "https://mausam.imd.gov.in"))
    usgs_feed_url: str = field(
        default_factory=lambda: _env(
            "DM_USGS_URL",
            "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
        )
    )
    firms_api_key: str = field(default_factory=lambda: _env("DM_FIRMS_KEY", ""))
    openmeteo_url: str = field(
        default_factory=lambda: _env("DM_OPENMETEO_URL", "https://api.open-meteo.com/v1/forecast")
    )

    # --- dispatch credentials (PRD Step 8) --------------------------------
    twilio_sid: str = field(default_factory=lambda: _env("DM_TWILIO_SID", ""))
    twilio_token: str = field(default_factory=lambda: _env("DM_TWILIO_TOKEN", ""))
    fcm_key: str = field(default_factory=lambda: _env("DM_FCM_KEY", ""))
    iridium_endpoint: str = field(default_factory=lambda: _env("DM_IRIDIUM_URL", ""))


settings = Settings()
