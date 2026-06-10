"""Tests for the structured-logging package (disastermind.logconfig)."""
from __future__ import annotations

import io
import json
import logging

import pytest

from disastermind.logconfig import (
    BoundLogger,
    JsonFormatter,
    bind,
    configure_logging,
    get_logger,
)
from disastermind.logconfig.core import ROOT_LOGGER_NAME, _HANDLER_TAG


@pytest.fixture()
def clean_root():
    """Snapshot/restore the disastermind root logger around each test."""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers = []
    try:
        yield logger
    finally:
        logger.handlers = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def _make_record(msg="hello", level=logging.INFO, name="disastermind.test", **extra):
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# --------------------------------------------------------------- JsonFormatter

def test_jsonformatter_emits_valid_json_with_expected_keys():
    fmt = JsonFormatter()
    line = fmt.format(_make_record(msg="boot complete", level=logging.WARNING))
    obj = json.loads(line)  # raises if not valid JSON
    assert obj["message"] == "boot complete"
    assert obj["level"] == "WARNING"
    assert obj["logger"] == "disastermind.test"
    assert "timestamp" in obj and isinstance(obj["timestamp"], str)


def test_jsonformatter_is_single_line():
    fmt = JsonFormatter()
    line = fmt.format(_make_record(msg="line one\nline two"))
    assert "\n" not in line
    assert json.loads(line)["message"] == "line one\nline two"


def test_jsonformatter_promotes_extra_fields():
    fmt = JsonFormatter()
    line = fmt.format(_make_record(incident_id="EQ-9", request_id="r-1"))
    obj = json.loads(line)
    assert obj["incident_id"] == "EQ-9"
    assert obj["request_id"] == "r-1"


def test_jsonformatter_serialises_exception():
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="disastermind.err",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    line = JsonFormatter().format(record)
    obj = json.loads(line)
    assert "exception" in obj
    assert "ValueError" in obj["exception"]
    assert "kaboom" in obj["exception"]


def test_jsonformatter_handles_non_serialisable_extra():
    fmt = JsonFormatter()
    line = fmt.format(_make_record(blob=object()))
    obj = json.loads(line)  # must not raise
    assert "blob" in obj and isinstance(obj["blob"], str)


def test_jsonformatter_static_fields():
    fmt = JsonFormatter(static_fields={"service": "disastermind", "version": "1.0"})
    obj = json.loads(fmt.format(_make_record()))
    assert obj["service"] == "disastermind"
    assert obj["version"] == "1.0"


# ----------------------------------------------------------- configure_logging

def test_configure_logging_idempotent_handler_count(clean_root):
    logger = configure_logging(level="INFO", fmt="json")
    owned = [h for h in logger.handlers if getattr(h, _HANDLER_TAG, False)]
    assert len(owned) == 1

    # Repeated calls must not stack handlers.
    for _ in range(5):
        configure_logging(level="DEBUG", fmt="text")
    owned = [h for h in logger.handlers if getattr(h, _HANDLER_TAG, False)]
    assert len(owned) == 1


def test_configure_logging_respects_level_arg(clean_root):
    logger = configure_logging(level="WARNING", fmt="json")
    assert logger.level == logging.WARNING
    logger = configure_logging(level=logging.DEBUG, fmt="json")
    assert logger.level == logging.DEBUG


def test_configure_logging_respects_env(monkeypatch, clean_root):
    monkeypatch.setenv("DM_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("DM_LOG_FORMAT", "json")
    logger = configure_logging()
    assert logger.level == logging.ERROR
    handler = next(h for h in logger.handlers if getattr(h, _HANDLER_TAG, False))
    assert isinstance(handler.formatter, JsonFormatter)


def test_configure_logging_default_level_is_info(monkeypatch, clean_root):
    monkeypatch.delenv("DM_LOG_LEVEL", raising=False)
    monkeypatch.delenv("DM_LOG_FORMAT", raising=False)
    logger = configure_logging()
    assert logger.level == logging.INFO


def test_configure_logging_json_output_roundtrip(clean_root):
    stream = io.StringIO()
    logger = configure_logging(level="INFO", fmt="json", stream=stream)
    logger.info("ingest ok", extra={"feed": "seismic"})
    line = stream.getvalue().strip()
    obj = json.loads(line)
    assert obj["message"] == "ingest ok"
    assert obj["feed"] == "seismic"
    assert obj["level"] == "INFO"


def test_configure_logging_text_output(clean_root):
    stream = io.StringIO()
    logger = configure_logging(level="INFO", fmt="text", stream=stream)
    logger.info("plain message")
    out = stream.getvalue()
    assert "plain message" in out
    # Text output is not JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.strip())


def test_configure_logging_invalid_inputs_fall_back(clean_root):
    logger = configure_logging(level="NOPE", fmt="weird")
    assert logger.level == logging.INFO
    handler = next(h for h in logger.handlers if getattr(h, _HANDLER_TAG, False))
    assert not isinstance(handler.formatter, JsonFormatter)


# ----------------------------------------------------------------------- bind

def test_bind_adds_fields_to_json(clean_root):
    stream = io.StringIO()
    configure_logging(level="INFO", fmt="json", stream=stream)
    log = bind(request_id="abc123", incident_id="EQ-42")
    assert isinstance(log, BoundLogger)
    log.info("dispatch accepted")
    obj = json.loads(stream.getvalue().strip())
    assert obj["request_id"] == "abc123"
    assert obj["incident_id"] == "EQ-42"
    assert obj["message"] == "dispatch accepted"


def test_bind_composes(clean_root):
    stream = io.StringIO()
    configure_logging(level="INFO", fmt="json", stream=stream)
    base = bind(request_id="r-1")
    narrowed = base.bind(incident_id="EQ-1")
    narrowed.info("ok")
    obj = json.loads(stream.getvalue().strip())
    assert obj["request_id"] == "r-1"
    assert obj["incident_id"] == "EQ-1"
    # Original binding is unchanged.
    assert "incident_id" not in base.fields


def test_bind_per_call_extra_overrides(clean_root):
    stream = io.StringIO()
    configure_logging(level="INFO", fmt="json", stream=stream)
    log = bind(incident_id="EQ-1")
    log.info("override", extra={"incident_id": "EQ-2"})
    obj = json.loads(stream.getvalue().strip())
    assert obj["incident_id"] == "EQ-2"


def test_bind_from_named_logger():
    log = bind("disastermind.custom", node="n1")
    assert log.logger.name == "disastermind.custom"
    assert log.fields["node"] == "n1"


def test_get_logger_namespacing():
    assert get_logger().name == ROOT_LOGGER_NAME
    assert get_logger("ingest").name == "disastermind.ingest"
    assert get_logger("disastermind.ingest").name == "disastermind.ingest"
