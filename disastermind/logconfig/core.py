"""Root logging configuration for DisasterMind.

:func:`configure_logging` installs a single handler on the ``disastermind``
logger and selects between JSON (for log aggregation) and human-readable text
output based on ``DM_LOG_FORMAT`` / the ``fmt`` argument. The level comes from
``DM_LOG_LEVEL`` / the ``level`` argument (default ``INFO``).

The call is **idempotent**: invoking it repeatedly reconfigures the existing
handler in place rather than stacking up duplicate handlers, so entry points
(``api.__main__``, ``cli``) may call it freely.

Pure stdlib — no third-party dependency, no network.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .formatter import JsonFormatter, text_formatter

ROOT_LOGGER_NAME = "disastermind"

# Marks the handler we own so we can find/reconfigure it on repeat calls
# without disturbing handlers installed by anything else (e.g. pytest, the
# stdlib root logger, third-party tools).
_HANDLER_TAG = "_dm_logconfig_handler"

_VALID_FORMATS = ("json", "text")


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the DisasterMind root logger, or a named child of it."""
    if not name or name == ROOT_LOGGER_NAME:
        return logging.getLogger(ROOT_LOGGER_NAME)
    if name.startswith(ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def _resolve_level(level: int | str | None) -> int:
    """Resolve an effective numeric level from arg, env, or default INFO."""
    raw: Any = level if level is not None else os.environ.get("DM_LOG_LEVEL")
    if raw is None or raw == "":
        return logging.INFO
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if text.isdigit():
        return int(text)
    resolved = logging.getLevelName(text.upper())
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _resolve_format(fmt: str | None) -> str:
    """Resolve the output format ('json' or 'text') from arg or env."""
    raw = fmt if fmt is not None else os.environ.get("DM_LOG_FORMAT")
    if raw is None:
        return "text"
    text = str(raw).strip().lower()
    if text in _VALID_FORMATS:
        return text
    return "text"


def _find_handler(logger: logging.Logger) -> logging.Handler | None:
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_TAG, False):
            return handler
    return None


def configure_logging(
    level: int | str | None = None,
    fmt: str | None = None,
    *,
    stream: Any | None = None,
    static_fields: dict[str, Any] | None = None,
) -> logging.Logger:
    """Configure the ``disastermind`` root logger and return it.

    Parameters
    ----------
    level:
        Logging level — an int, a name (``"DEBUG"``), or ``None`` to read
        ``DM_LOG_LEVEL`` (falling back to ``INFO``).
    fmt:
        ``"json"`` for one-JSON-object-per-line output (log aggregation), or
        ``"text"`` for human-readable output. ``None`` reads ``DM_LOG_FORMAT``
        (falling back to ``"text"``).
    stream:
        Optional stream for the handler (defaults to ``sys.stderr`` via
        :class:`logging.StreamHandler`). Mainly for tests.
    static_fields:
        Fields attached to every JSON record (e.g. ``service``/``version``).

    Idempotent: repeated calls reconfigure the single owned handler rather than
    adding new ones. Returns the configured logger.
    """
    resolved_level = _resolve_level(level)
    resolved_fmt = _resolve_format(fmt)

    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(resolved_level)
    # Don't propagate to the (often handler-less / differently-configured)
    # stdlib root logger; we own our output.
    logger.propagate = False

    handler = _find_handler(logger)
    if handler is None:
        handler = logging.StreamHandler(stream) if stream is not None else logging.StreamHandler()
        setattr(handler, _HANDLER_TAG, True)
        logger.addHandler(handler)
    elif stream is not None:
        # Re-point the existing owned handler at the new stream.
        try:
            handler.setStream(stream)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - non-stream handler
            pass

    if resolved_fmt == "json":
        handler.setFormatter(JsonFormatter(static_fields=static_fields))
    else:
        handler.setFormatter(text_formatter())

    handler.setLevel(resolved_level)
    return logger
