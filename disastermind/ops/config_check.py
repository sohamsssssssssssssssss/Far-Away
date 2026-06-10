"""Settings validation for production readiness (PRD Step 10).

:func:`validate_settings` inspects a :class:`~disastermind.core.config.Settings`
(or any duck-typed object with the same attributes) and returns a list of
:class:`Issue` records describing configuration problems — *without* touching the
network or any backend. It is the pre-flight check an operator runs before
arming a live deployment ("are my intervals sane, are my DSNs well-formed?").

Severity model mirrors the diagnostics doctor:

  * ``error``   — would break or unsafely run the system (e.g. loop interval <= 0).
  * ``warning`` — degraded / risky but operable (e.g. a credential left blank, so
    that dispatch channel is unavailable).

An empty list means the settings passed clean. Everything here is stdlib-only,
deterministic, and side-effect free — well-formedness of a DSN is checked by
*parsing the string*, never by connecting.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable
from urllib.parse import urlsplit


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """One configuration problem found by :func:`validate_settings`."""

    field: str
    severity: Severity
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    def to_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "severity": self.severity.value,
            "message": self.message,
        }

    def __str__(self) -> str:  # pragma: no cover - convenience
        return f"[{self.severity.value}] {self.field}: {self.message}"


#: DSN schemes we recognise as well-formed Postgres/Timescale connection strings.
_POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})


def _is_positive_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0


def _dsn_issue(field: str, dsn: str) -> Issue | None:
    """Return an :class:`Issue` if ``dsn`` is set but not a well-formed PG DSN.

    Only validates when a value is present (an empty DSN is handled by the
    caller). Parsing is purely lexical: scheme must be ``postgres(ql)`` and a
    host or path (database name) must be present. We never open a socket.
    """
    if not dsn:
        return None
    try:
        parts = urlsplit(dsn)
    except ValueError as exc:
        return Issue(field, Severity.ERROR, f"DSN is not parseable: {exc}")
    scheme = (parts.scheme or "").lower()
    if scheme not in _POSTGRES_SCHEMES:
        return Issue(
            field,
            Severity.ERROR,
            f"DSN scheme must be one of {sorted(_POSTGRES_SCHEMES)} (got {scheme or 'none'!r})",
        )
    # A usable PG DSN needs at least a host (netloc) or a database (path).
    if not parts.netloc and not parts.path.strip("/"):
        return Issue(field, Severity.ERROR, "DSN has neither a host nor a database name")
    return None


def _url_issue(field: str, url: str, *, required: bool = False) -> Issue | None:
    """Validate an http(s) URL when present (or flag it missing if required)."""
    if not url:
        if required:
            return Issue(field, Severity.ERROR, "is empty but required")
        return None
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return Issue(field, Severity.ERROR, f"URL is not parseable: {exc}")
    if parts.scheme not in {"http", "https"}:
        return Issue(
            field,
            Severity.WARNING,
            f"URL scheme should be http/https (got {parts.scheme or 'none'!r})",
        )
    if not parts.netloc:
        return Issue(field, Severity.WARNING, "URL has no host")
    return None


def validate_settings(settings: Any) -> list[Issue]:
    """Validate ``settings`` and return a list of :class:`Issue` (empty == clean).

    Checks (all offline / lexical):

      * ``loop_interval_seconds``        — must be a positive number (ERROR).
      * ``escalation_timeout_seconds``   — must be a positive number (ERROR).
      * ``grid_cell_meters``             — positive if present (WARNING otherwise).
      * ``postgres_dsn`` / ``timescale_dsn`` — required & well-formed PG DSN
        (ERROR if empty or malformed).
      * ``elasticsearch_url`` / feed URLs — well-formed http(s) if set (WARNING).
      * ``use_kafka`` true but ``kafka_brokers`` empty (ERROR — can't connect).
      * cooldown sanity: escalation timeout should exceed the loop interval, else
        a timeout can never be observed across a cycle (WARNING).
    """
    issues: list[Issue] = []

    # --- coordination loop timings ---------------------------------------
    interval = getattr(settings, "loop_interval_seconds", None)
    if not _is_positive_number(interval):
        issues.append(
            Issue(
                "loop_interval_seconds",
                Severity.ERROR,
                f"must be a positive number (got {interval!r})",
            )
        )

    timeout = getattr(settings, "escalation_timeout_seconds", None)
    if not _is_positive_number(timeout):
        issues.append(
            Issue(
                "escalation_timeout_seconds",
                Severity.ERROR,
                f"must be a positive number (got {timeout!r})",
            )
        )

    # Cooldown sanity only when both are usable positive numbers.
    if _is_positive_number(interval) and _is_positive_number(timeout) and timeout < interval:
        issues.append(
            Issue(
                "escalation_timeout_seconds",
                Severity.WARNING,
                f"escalation timeout ({timeout}s) is shorter than the loop interval "
                f"({interval}s) — timeouts may never be observed",
            )
        )

    grid = getattr(settings, "grid_cell_meters", None)
    if grid is not None and not _is_positive_number(grid):
        issues.append(
            Issue(
                "grid_cell_meters",
                Severity.WARNING,
                f"should be a positive number (got {grid!r})",
            )
        )

    # --- storage DSNs (required + well-formed) ----------------------------
    for field_name in ("postgres_dsn", "timescale_dsn"):
        dsn = getattr(settings, field_name, "")
        if not dsn:
            issues.append(
                Issue(field_name, Severity.ERROR, "is empty — no storage DSN configured")
            )
            continue
        dsn_issue = _dsn_issue(field_name, dsn)
        if dsn_issue is not None:
            issues.append(dsn_issue)

    # --- optional URLs (well-formed if set) -------------------------------
    for field_name in ("elasticsearch_url", "imd_base_url", "usgs_feed_url", "openmeteo_url"):
        url = getattr(settings, field_name, "")
        url_issue = _url_issue(field_name, url)
        if url_issue is not None:
            issues.append(url_issue)

    # --- Kafka consistency ------------------------------------------------
    if bool(getattr(settings, "use_kafka", False)):
        brokers = getattr(settings, "kafka_brokers", "")
        if not brokers:
            issues.append(
                Issue(
                    "kafka_brokers",
                    Severity.ERROR,
                    "use_kafka is enabled but kafka_brokers is empty",
                )
            )

    return issues


def errors(issues: Iterable[Issue]) -> list[Issue]:
    """Filter ``issues`` down to the ERROR-severity ones."""
    return [i for i in issues if i.is_error]


def is_valid(settings: Any) -> bool:
    """True iff ``settings`` has no ERROR-severity issues (warnings allowed)."""
    return not errors(validate_settings(settings))


__all__ = [
    "Issue",
    "Severity",
    "validate_settings",
    "errors",
    "is_valid",
]
