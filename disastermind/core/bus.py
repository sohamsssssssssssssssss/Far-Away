"""Message bus abstraction.

PRD Step 10 (Graceful Degradation): "If message bus (Kafka) is down: agents
operate independently on last-known state; auto-failover to backup Kafka cluster."

We provide:
  * ``MessageBus``   — the interface agents code against.
  * ``InMemoryBus``  — synchronous, single-process; used by the orchestration
                       loop, tests, and as the degraded fallback.
  * ``KafkaBus``     — thin adapter that lazily imports a Kafka client and falls
                       back to an :class:`InMemoryBus` when brokers are
                       unreachable (failover behaviour).
"""
from __future__ import annotations

import abc
import logging
from collections import defaultdict
from collections.abc import Callable

from .contracts import Message

log = logging.getLogger("disastermind.bus")

Callback = Callable[[Message], None]


class MessageBus(abc.ABC):
    @abc.abstractmethod
    def publish(self, message: Message) -> None: ...

    @abc.abstractmethod
    def subscribe(self, topic: str, subscriber: str, callback: Callback) -> None: ...

    def close(self) -> None:  # pragma: no cover - optional override  # noqa: B027
        pass


class InMemoryBus(MessageBus):
    """Synchronous in-process bus.

    Dispatch is immediate: ``publish`` invokes every subscriber callback before
    returning. This keeps the 30-second coordination loop deterministic and makes
    unit testing trivial. A small ring buffer retains recent messages so newly
    started agents (and the audit layer) can replay last-known state.
    """

    def __init__(self, history: int = 2000) -> None:
        self._subs: dict[str, list[tuple[str, Callback]]] = defaultdict(list)
        self._history_max = history
        self.history: list[Message] = []

    def subscribe(self, topic: str, subscriber: str, callback: Callback) -> None:
        self._subs[topic].append((subscriber, callback))
        log.debug("%s subscribed to %s", subscriber, topic)

    def publish(self, message: Message) -> None:
        self.history.append(message)
        if len(self.history) > self._history_max:
            self.history = self.history[-self._history_max :]
        for subscriber, cb in list(self._subs.get(message.topic, [])):
            try:
                cb(message)
            except Exception:  # an agent failing must not take down the bus (Step 10)
                log.exception("subscriber %s raised on topic %s", subscriber, message.topic)

    def last_on(self, topic: str) -> Message | None:
        for m in reversed(self.history):
            if m.topic == topic:
                return m
        return None


class KafkaBus(MessageBus):
    """Kafka-backed bus with automatic degradation to in-memory.

    The real client (``confluent_kafka``) is imported lazily so the package
    installs and the test-suite runs without a broker present. If the primary
    brokers are unreachable we try the backup cluster, then fall back to an
    in-memory bus and log a degradation event — mirroring PRD Step 10.
    """

    def __init__(
        self,
        brokers: str,
        backup_brokers: str | None = None,
        client_id: str = "disastermind",
    ) -> None:
        self.brokers = brokers
        self.backup_brokers = backup_brokers
        self.client_id = client_id
        self._fallback = InMemoryBus()
        self._producer = None
        self.degraded = False
        self._connect()

    def _connect(self) -> None:
        try:
            from confluent_kafka import Producer  # type: ignore

            for endpoint in filter(None, [self.brokers, self.backup_brokers]):
                try:
                    self._producer = Producer(
                        {"bootstrap.servers": endpoint, "client.id": self.client_id}
                    )
                    log.info("KafkaBus connected to %s", endpoint)
                    return
                except Exception:
                    log.warning("KafkaBus failed to connect to %s, trying next", endpoint)
            raise RuntimeError("no reachable broker")
        except Exception:
            self.degraded = True
            log.error("KafkaBus DEGRADED — operating on in-memory fallback (PRD Step 10)")

    def publish(self, message: Message) -> None:
        if self._producer is None:
            self._fallback.publish(message)
            return
        try:
            import json

            self._producer.produce(message.topic, json.dumps(message.to_dict()).encode())
            self._producer.poll(0)
        except Exception:
            log.exception("Kafka publish failed; degrading to in-memory")
            self.degraded = True
            self._fallback.publish(message)

    def subscribe(self, topic: str, subscriber: str, callback: Callback) -> None:
        # Consumer loop is started by the runtime; in degraded mode we use the
        # in-memory fan-out so the single-process loop still functions.
        self._fallback.subscribe(topic, subscriber, callback)
