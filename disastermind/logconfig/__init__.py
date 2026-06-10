"""Production structured logging for DisasterMind.

Log aggregation backends (Loki, ELK, CloudWatch) want one JSON object per
line; humans want readable text. This package provides both behind a single
idempotent entry point.

Usage (entry points such as ``api.__main__`` and ``cli`` MAY call this — they
are not modified here)::

    from disastermind.logconfig import configure_logging, bind

    configure_logging()                 # DM_LOG_FORMAT / DM_LOG_LEVEL aware
    configure_logging(level="DEBUG", fmt="json")

    log = bind(request_id="r-1", incident_id="EQ-7")
    log.info("dispatch accepted")       # JSON includes request_id/incident_id

Environment:
  * ``DM_LOG_FORMAT`` — ``json`` or ``text`` (default ``text``).
  * ``DM_LOG_LEVEL``  — level name or number (default ``INFO``).

Everything is pure stdlib — no third-party dependency, no network.
"""
from __future__ import annotations

from .context import BoundLogger, bind
from .core import ROOT_LOGGER_NAME, configure_logging, get_logger
from .formatter import JsonFormatter, text_formatter

__all__ = [
    "configure_logging",
    "JsonFormatter",
    "bind",
    "BoundLogger",
    "get_logger",
    "text_formatter",
    "ROOT_LOGGER_NAME",
]
