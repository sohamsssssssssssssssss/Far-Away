"""Backend liveness probe for the real integration adapters (PRD Step 10).

:func:`ping_backends` reports, per external backend, one of three states:

  * ``"absent"`` — not configured (empty DSN/URL/brokers) **or** the client
    library is not installed: the system runs on its in-memory fallback.
  * ``"down"``   — configured and the library is present, but the live probe
    failed (server unreachable / auth error).
  * ``"ok"``     — configured, library present, and the probe succeeded.

Every backend client (``confluent_kafka``, ``psycopg``, ``elasticsearch``,
``minio``) is imported *lazily* and wrapped in try/except, so this module has NO
import-time dependency and NO import-time network. ``ping_backends`` **never
raises** — a health check must not take the system down (PRD Step 10). With no
optional libraries and the default :class:`~disastermind.core.config.Settings`,
every backend degrades to ``"absent"``.
"""
from __future__ import annotations

import logging
import socket
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger("disastermind.integrations.health")

ABSENT = "absent"
DOWN = "down"
OK = "ok"

# Backends this probe knows about (stable order for dashboards).
BACKENDS: tuple[str, ...] = ("kafka", "postgis", "timescale", "elasticsearch", "minio")


def _tcp_reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    """Best-effort TCP connect probe; never raises."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
    except Exception:  # pragma: no cover - defensive: probe must not raise
        return False


def _hostport_from_dsn(dsn: str, default_port: int) -> tuple[str, int] | None:
    """Parse ``host:port`` from a libpq URL/keyword DSN. None if not extractable."""
    if not dsn:
        return None
    try:
        if "://" in dsn:
            parsed = urlparse(dsn)
            host = parsed.hostname or "localhost"
            port = parsed.port or default_port
            return host, int(port)
        # keyword DSN: "host=... port=..."
        host, port = "localhost", default_port
        for token in dsn.split():
            if token.startswith("host="):
                host = token[len("host=") :] or host
            elif token.startswith("port="):
                try:
                    port = int(token[len("port=") :])
                except ValueError:
                    pass
        return host, port
    except Exception:  # pragma: no cover - defensive
        return None


def _ping_kafka(brokers: str) -> str:
    if not brokers:
        return ABSENT
    try:
        import confluent_kafka  # type: ignore  # noqa: F401
    except Exception:
        return ABSENT
    endpoint = brokers.split(",")[0].strip()
    host, _, port = endpoint.partition(":")
    host = host or "localhost"
    try:
        port_n = int(port) if port else 9092
    except ValueError:
        port_n = 9092
    return OK if _tcp_reachable(host, port_n) else DOWN


def _ping_postgres(dsn: str) -> str:
    if not dsn:
        return ABSENT
    try:
        import psycopg  # type: ignore  # noqa: F401
    except Exception:
        return ABSENT
    hp = _hostport_from_dsn(dsn, 5432)
    if hp is None:
        return DOWN
    return OK if _tcp_reachable(*hp) else DOWN


def _ping_elasticsearch(url: str) -> str:
    if not url:
        return ABSENT
    try:
        import elasticsearch  # type: ignore  # noqa: F401
    except Exception:
        return ABSENT
    hp = _hostport_from_dsn(url, 9200)
    if hp is None:
        return DOWN
    return OK if _tcp_reachable(*hp) else DOWN


def _ping_minio(endpoint: str) -> str:
    if not endpoint:
        return ABSENT
    try:
        import minio  # type: ignore  # noqa: F401
    except Exception:
        return ABSENT
    hp = _hostport_from_dsn(
        endpoint if "://" in endpoint else f"//{endpoint}", 9000
    )
    if hp is None:
        return DOWN
    return OK if _tcp_reachable(*hp) else DOWN


def ping_backends(settings: Any) -> dict[str, str]:
    """Return ``{backend: 'absent'|'down'|'ok'}`` for every external backend.

    Lazy + defensive: reads endpoints off ``settings`` (a
    :class:`~disastermind.core.config.Settings` or any object exposing the same
    attributes); a missing attribute is treated as unconfigured (``absent``).
    Never raises.
    """
    def _attr(name: str, default: str = "") -> str:
        return str(getattr(settings, name, default) or "")

    result: dict[str, str] = {}
    for backend in BACKENDS:
        try:
            if backend == "kafka":
                brokers = _attr("kafka_brokers") if _bool(settings, "use_kafka", True) else ""
                result[backend] = _ping_kafka(brokers)
            elif backend == "postgis":
                result[backend] = _ping_postgres(_attr("postgres_dsn"))
            elif backend == "timescale":
                result[backend] = _ping_postgres(_attr("timescale_dsn"))
            elif backend == "elasticsearch":
                result[backend] = _ping_elasticsearch(_attr("elasticsearch_url"))
            elif backend == "minio":
                result[backend] = _ping_minio(
                    _attr("minio_endpoint") or _attr("s3_endpoint")
                )
        except Exception:  # pragma: no cover - probe must never raise
            log.exception("backend probe for %s raised; reporting 'down'", backend)
            result[backend] = DOWN
    return result


def _bool(settings: Any, name: str, default: bool) -> bool:
    val = getattr(settings, name, default)
    return bool(val) if val is not None else default
