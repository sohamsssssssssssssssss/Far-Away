"""Process runtime package (PRD Step 10).

Wraps the wired coordination loop in a long-lived, signal-aware supervisor
(:class:`ProcessRunner`) and provides a real-but-lazy Kafka consumer loop
(:class:`KafkaConsumerRuntime`) that degrades to a clean no-op over the
in-memory fallback when no broker / ``confluent_kafka`` is available.

Stdlib-only at import time; the Kafka client is imported lazily inside methods.
"""
from __future__ import annotations

from .consumer import KafkaConsumerRuntime
from .runner import ProcessRunner

__all__ = ["ProcessRunner", "KafkaConsumerRuntime"]
