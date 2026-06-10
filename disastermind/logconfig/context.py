"""Contextual field binding for structured logging.

:func:`bind` returns a :class:`logging.LoggerAdapter` that injects fixed
contextual fields (``request_id``, ``incident_id``, …) into every record it
emits. Because the fields land in ``record.__dict__`` they are picked up by
:class:`~disastermind.logconfig.formatter.JsonFormatter` and surfaced as
top-level JSON keys.

Bindings compose: calling :meth:`BoundLogger.bind` on an already-bound logger
merges the new fields over the inherited ones, so a request-scoped logger can
narrow to an incident-scoped one without losing context.
"""
from __future__ import annotations

import logging
from typing import Any

from .core import get_logger


class BoundLogger(logging.LoggerAdapter):
    """A :class:`logging.LoggerAdapter` carrying structured context fields."""

    def __init__(self, logger: logging.Logger, fields: dict[str, Any]) -> None:
        super().__init__(logger, dict(fields))

    @property
    def fields(self) -> dict[str, Any]:
        """The contextual fields attached to this adapter (a copy)."""
        return dict(self.extra or {})

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        # Merge our bound fields into the record's ``extra`` so they become
        # record attributes. Per-call extras win over bound fields.
        extra = dict(self.extra or {})
        call_extra = kwargs.get("extra")
        if call_extra:
            extra.update(call_extra)
        kwargs["extra"] = extra
        return msg, kwargs

    def bind(self, **fields: Any) -> "BoundLogger":
        """Return a new adapter with ``fields`` merged over the current ones."""
        merged = dict(self.extra or {})
        merged.update(fields)
        return BoundLogger(self.logger, merged)


def bind(logger: logging.Logger | logging.LoggerAdapter | str | None = None, **fields: Any) -> BoundLogger:
    """Attach contextual ``fields`` to a logger for structured output.

    ``logger`` may be a :class:`logging.Logger`, an existing adapter (its
    fields are inherited), a logger name, or ``None`` (the root DisasterMind
    logger). Example::

        log = bind(request_id="abc123", incident_id="EQ-42")
        log.info("dispatch accepted")   # -> JSON includes request_id/incident_id
    """
    base_fields: dict[str, Any] = {}
    if isinstance(logger, logging.LoggerAdapter):
        base_fields.update(logger.extra or {})
        underlying = logger.logger
    elif isinstance(logger, logging.Logger):
        underlying = logger
    elif isinstance(logger, str):
        underlying = logging.getLogger(logger)
    else:
        underlying = get_logger()

    base_fields.update(fields)
    return BoundLogger(underlying, base_fields)
