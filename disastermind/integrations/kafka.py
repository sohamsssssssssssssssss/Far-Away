"""Kafka publish -> consume round-trip adapter (PRD Step 1/9 message bus).

The DisasterMind bus (:mod:`disastermind.core.bus`) speaks
:class:`~disastermind.core.contracts.Message`; this module is the *integration*
helper that actually serialises a Message to the wire (JSON dict, matching
``Message.to_dict()``), produces it to a Kafka topic and consumes it back —
exactly the shape the live integration test
``tests/integration/test_kafka_roundtrip.py`` exercises.

Offline-safe (PRD Step 10 graceful degradation):
  * The ``confluent_kafka`` client is imported *lazily* inside :meth:`_connect`,
    wrapped in try/except. NO import-time network, NO import-time dependency.
  * When the client is absent OR the brokers are unreachable, the adapter
    degrades to an **in-memory topic store** (a per-topic list of frames) so
    ``produce`` / ``consume`` still round-trip deterministically with no broker.

Wire format: every frame is ``(key, value_bytes)`` where ``value_bytes`` is the
UTF-8 JSON encoding of the message dict — identical to what
:class:`disastermind.core.bus.KafkaBus` emits, so a degraded round-trip is
byte-for-byte the same payload a live broker would carry.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

log = logging.getLogger("disastermind.integrations.kafka")

# Default broker endpoint mirrors docker-compose's `kafka` service / the live test.
DEFAULT_BOOTSTRAP = "localhost:9092"


def message_to_frame(message: Any) -> tuple[str, bytes]:
    """Serialise a Message (or dict) into a ``(key, value_bytes)`` Kafka frame.

    ``key`` is the message id (used for partition affinity / dedupe); ``value``
    is the UTF-8 JSON of the message dict, matching ``Message.to_dict()``.
    """
    to_dict = getattr(message, "to_dict", None)
    doc = to_dict() if callable(to_dict) else dict(message)
    key = str(doc.get("id", ""))
    return key, json.dumps(doc).encode("utf-8")


def frame_to_dict(value: bytes | str) -> dict[str, Any]:
    """Decode a consumed frame's value back into the message dict."""
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    out = json.loads(value)
    if not isinstance(out, dict):
        raise TypeError(f"expected a JSON object frame, got {type(out)!r}")
    return out


class KafkaRoundTrip:
    """Produce + consume Message dicts against Kafka, degrading to in-memory.

    Construct with the broker endpoint; pass an empty string to force the
    offline in-memory mode. :attr:`is_fallback` reports which mode is active.
    No network happens at construction unless ``connect=True`` is requested AND a
    non-empty ``bootstrap`` is given (the live test path); the default is offline.
    """

    def __init__(
        self,
        bootstrap: str = "",
        *,
        client_id: str = "disastermind",
        connect: bool = False,
    ) -> None:
        self.bootstrap = bootstrap
        self.client_id = client_id
        # in-memory degraded store: topic -> list[(key, value_bytes)]
        self._store: dict[str, list[tuple[str, bytes]]] = defaultdict(list)
        # per-(topic,group) consume cursor for the in-memory store
        self._offsets: dict[tuple[str, str], int] = defaultdict(int)
        self._producer = None
        if connect and bootstrap:
            self._producer = self._connect(bootstrap)

    @property
    def is_fallback(self) -> bool:
        """True when running on the in-memory store (no live producer)."""
        return self._producer is None

    def _connect(self, bootstrap: str):  # pragma: no cover - optional dep/network
        try:
            from confluent_kafka import Producer  # type: ignore

            return Producer(
                {"bootstrap.servers": bootstrap, "client.id": self.client_id}
            )
        except Exception:
            log.warning(
                "confluent_kafka unavailable / broker unreachable; "
                "in-memory Kafka round-trip fallback (PRD Step 10)"
            )
            return None

    # --------------------------------------------------------------- produce ----
    def produce(self, topic: str, message: Any) -> tuple[str, bytes]:
        """Produce one Message (or dict) to ``topic``; returns the wire frame."""
        key, value = message_to_frame(message)
        if self._producer is None:
            self._store[topic].append((key, value))
            return key, value
        return self._produce_kafka(topic, key, value)  # pragma: no cover

    def produce_many(self, topic: str, messages: list[Any]) -> int:
        for m in messages:
            self.produce(topic, m)
        return len(messages)

    # --------------------------------------------------------------- consume ----
    def consume(
        self,
        topic: str,
        *,
        group: str = "disastermind",
        max_messages: int = 1,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        """Consume up to ``max_messages`` message dicts from ``topic``.

        In-memory mode advances a per-(topic, group) offset so repeated calls
        page through produced frames (earliest-first), mirroring a real consumer
        group with ``auto.offset.reset=earliest``.
        """
        if self._producer is not None:
            return self._consume_kafka(topic, group, max_messages, timeout)  # pragma: no cover
        frames = self._store.get(topic, [])
        cursor = self._offsets[(topic, group)]
        out: list[dict[str, Any]] = []
        while cursor < len(frames) and len(out) < max_messages:
            _key, value = frames[cursor]
            out.append(frame_to_dict(value))
            cursor += 1
        self._offsets[(topic, group)] = cursor
        return out

    def roundtrip(
        self,
        topic: str,
        message: Any,
        *,
        group: str = "disastermind",
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Produce one message then consume it straight back; returns the dict."""
        self.produce(topic, message)
        got = self.consume(topic, group=group, max_messages=1, timeout=timeout)
        if not got:  # pragma: no cover - only reachable on a live broker timeout
            raise TimeoutError(f"no message consumed from {topic!r} within {timeout}s")
        return got[0]

    def close(self) -> None:
        if self._producer is not None:  # pragma: no cover
            try:
                self._producer.flush(5)
            except Exception:
                log.exception("error flushing Kafka producer")
            self._producer = None

    # ------------------------------------------------- confluent_kafka (lazy) ---
    def _produce_kafka(self, topic: str, key: str, value: bytes):  # pragma: no cover
        try:
            self._producer.produce(topic, value=value, key=key or None)
            self._producer.poll(0)
            self._producer.flush(15)
        except Exception:
            log.exception("Kafka produce failed; buffering in memory")
            self._store[topic].append((key, value))
        return key, value

    def _consume_kafka(self, topic, group, max_messages, timeout):  # pragma: no cover
        import time

        from confluent_kafka import Consumer  # type: ignore

        consumer = Consumer(
            {
                "bootstrap.servers": self.bootstrap,
                "group.id": group,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        out: list[dict[str, Any]] = []
        try:
            consumer.subscribe([topic])
            deadline = time.time() + timeout
            while time.time() < deadline and len(out) < max_messages:
                rec = consumer.poll(1.0)
                if rec is None or rec.error():
                    continue
                out.append(frame_to_dict(rec.value()))
        finally:
            consumer.close()
        return out
