"""Stdlib :class:`logging.Formatter` subclasses for DisasterMind.

Production log aggregation (Loki/ELK/CloudWatch) ingests one structured JSON
object per line. :class:`JsonFormatter` renders every :class:`logging.LogRecord`
as a single-line JSON document carrying the canonical fields plus any
contextual ``extra=`` fields and exception/stack information. The text
formatter is a human-readable fallback for local development.

Pure stdlib — no third-party dependency, no network.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

# Attributes present on a vanilla LogRecord. Anything *not* in this set was
# attached by the caller via ``logger.info(msg, extra={...})`` (or a bound
# adapter) and is therefore promoted to a top-level JSON field.
_RESERVED: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        # Our own canonical keys (never duplicated as "extra"):
        "message",
        "asctime",
    }
)

# Canonical top-level keys we always emit; protected from being shadowed by an
# accidentally-named extra field.
_CANONICAL: frozenset[str] = frozenset(
    {"timestamp", "level", "logger", "message"}
)

# Human-readable text format used when JSON is not requested.
DEFAULT_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as one line of JSON.

    Always emits ``timestamp`` (ISO-8601, UTC), ``level``, ``logger`` and
    ``message``. Exception and stack info, when present, are added under
    ``exception``/``stack``. Any non-reserved record attribute (i.e. a field
    passed via ``extra=`` or a :class:`~disastermind.logconfig.context.BoundLogger`)
    is promoted to a top-level key.

    Usable standalone::

        handler.setFormatter(JsonFormatter())
    """

    def __init__(
        self,
        *,
        ensure_ascii: bool = False,
        sort_keys: bool = False,
        static_fields: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._ensure_ascii = ensure_ascii
        self._sort_keys = sort_keys
        self._static_fields = dict(static_fields or {})

    def _timestamp(self, record: logging.LogRecord) -> str:
        # Use the record's own creation time (not "now") so the timestamp
        # reflects when the event happened. UTC with offset for unambiguity.
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.isoformat()

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib API
        payload: dict[str, Any] = {
            "timestamp": self._timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Static fields configured on the formatter (e.g. service/version).
        for key, value in self._static_fields.items():
            if key not in _CANONICAL:
                payload[key] = value

        # Promote caller-supplied extras to top-level keys.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in _CANONICAL:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value

        # Exception / stack information.
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(
            payload,
            ensure_ascii=self._ensure_ascii,
            sort_keys=self._sort_keys,
            default=_json_default,
        )


def _json_default(obj: Any) -> str:
    """Best-effort serialisation for non-JSON-native extra values."""
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            return repr(obj)
    return str(obj)


def text_formatter(fmt: str | None = None, datefmt: str | None = None) -> logging.Formatter:
    """Build the human-readable text formatter (dev fallback)."""
    return logging.Formatter(
        fmt or DEFAULT_TEXT_FORMAT,
        datefmt=datefmt or DEFAULT_DATE_FORMAT,
    )
