"""Live Kafka publish -> consume round-trip (docker-compose `kafka` service).

Gated by tests/integration/conftest.py (collected only when DM_INTEGRATION=1).
Self-skips cleanly when confluent_kafka is missing or the broker is unreachable.
"""
from __future__ import annotations

import json
import socket
import time
import uuid

import pytest

# Skip the whole module if the client lib is absent (never errors on import).
confluent_kafka = pytest.importorskip("confluent_kafka")
from confluent_kafka import Consumer, Producer  # noqa: E402

from disastermind.core.contracts import Message, MessageType, Priority  # noqa: E402

KAFKA_HOST = "localhost"
KAFKA_PORT = 9092
BOOTSTRAP = f"{KAFKA_HOST}:{KAFKA_PORT}"


def _reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(KAFKA_HOST, KAFKA_PORT),
    reason=f"Kafka unreachable at {BOOTSTRAP} (start `docker compose up -d kafka`)",
)


def test_kafka_message_roundtrip():
    topic = f"dm-it-{uuid.uuid4().hex[:12]}"
    group = f"dm-it-group-{uuid.uuid4().hex[:12]}"

    msg = Message(
        sender="tier3.ingestion.usgs",
        recipient="tier2.prediction",
        type=MessageType.ALERT,
        priority=Priority.CRITICAL,
        payload={"kind": "earthquake", "magnitude": 6.4, "depth_km": 10},
        reasoning=["USGS M6.4 within 50km of populated grid cell"],
    )
    payload = json.dumps(msg.to_dict()).encode("utf-8")

    producer = Producer({"bootstrap.servers": BOOTSTRAP})
    producer.produce(topic, value=payload, key=msg.id)
    producer.flush(15)

    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    try:
        consumer.subscribe([topic])
        received = None
        deadline = time.time() + 30.0
        while time.time() < deadline:
            rec = consumer.poll(1.0)
            if rec is None:
                continue
            if rec.error():
                # transient (e.g. unknown topic until auto-created) -> keep polling
                continue
            received = rec
            break
        assert received is not None, "did not consume the produced message within 30s"
        round_tripped = json.loads(received.value().decode("utf-8"))
    finally:
        consumer.close()

    # Payload round-trips faithfully through the broker.
    assert round_tripped == msg.to_dict()
    assert round_tripped["id"] == msg.id
    assert round_tripped["type"] == MessageType.ALERT.value
    assert round_tripped["priority"] == int(Priority.CRITICAL)
    assert round_tripped["payload"]["magnitude"] == 6.4
