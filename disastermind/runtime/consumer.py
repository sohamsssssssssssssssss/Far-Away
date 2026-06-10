"""Real Kafka consumer runtime (PRD Step 10 — process runtime / failover).

A :class:`KafkaConsumerRuntime` turns a :class:`~disastermind.core.bus.KafkaBus`
plus a set of ``topic -> callback`` subscriptions into a live ``confluent_kafka``
consumer loop running on a daemon background thread. The Kafka client is imported
**lazily** so the package installs and the whole test-suite runs with the Python
standard library only and no broker present.

Graceful degradation (PRD Step 10): if the bus is degraded, the ``confluent_kafka``
library is absent, or the broker is unreachable, the runtime becomes a clean
**no-op** over the in-memory fallback and sets :attr:`degraded` ``True``. The
single-process :class:`~disastermind.orchestration.loop.CoordinationLoop` still
fans messages out synchronously through ``InMemoryBus`` in that mode, so no
events are lost — the runtime simply does not spin a real consumer thread.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ..core.bus import KafkaBus, MessageBus
from ..core.contracts import Message

log = logging.getLogger("disastermind.runtime.consumer")

Callback = Callable[[Message], None]


class KafkaConsumerRuntime:
    """Background Kafka consumer loop with a clean in-memory fallback.

    Parameters
    ----------
    bus:
        The message bus the system was built on. Only a non-degraded
        :class:`~disastermind.core.bus.KafkaBus` triggers a real consumer thread;
        any other bus (or a degraded ``KafkaBus``) keeps the runtime in no-op
        degraded mode and relies on the synchronous in-memory fan-out.
    subscriptions:
        Mapping of Kafka topic name to a callback invoked for each decoded
        :class:`~disastermind.core.contracts.Message`.
    group_id:
        Kafka consumer-group id (live mode only).
    poll_timeout:
        Seconds passed to ``consumer.poll`` each iteration (live mode only).
    """

    def __init__(
        self,
        bus: MessageBus,
        subscriptions: dict[str, Callback] | None = None,
        group_id: str = "disastermind-runtime",
        poll_timeout: float = 1.0,
    ) -> None:
        self.bus = bus
        self.subscriptions: dict[str, Callback] = dict(subscriptions or {})
        self.group_id = group_id
        self.poll_timeout = poll_timeout

        self.degraded = True
        self._consumer = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._evaluate_mode()

    # ------------------------------------------------------------------ wiring
    def subscribe(self, topic: str, callback: Callback) -> None:
        """Register a ``topic -> callback`` subscription before :meth:`start`.

        In degraded (in-memory) mode the callback is also wired onto the
        fallback bus so the single-process loop still delivers messages.
        """
        self.subscriptions[topic] = callback
        if self.degraded:
            try:
                self.bus.subscribe(topic, self.group_id, callback)
            except Exception:  # pragma: no cover - defensive (Step 10)
                log.exception("degraded subscribe to %s failed (continuing)", topic)

    def _evaluate_mode(self) -> None:
        """Decide live vs degraded: live only for a healthy KafkaBus + lib."""
        if not isinstance(self.bus, KafkaBus):
            log.info("KafkaConsumerRuntime: non-Kafka bus -> degraded no-op (Step 10)")
            self.degraded = True
            return
        if getattr(self.bus, "degraded", True) or getattr(self.bus, "_producer", None) is None:
            log.warning("KafkaConsumerRuntime: KafkaBus degraded -> in-memory fallback (Step 10)")
            self.degraded = True
            return
        # A producer connected; we *may* be able to consume. Confirm the client
        # is importable — if not, degrade. The Consumer itself is created in
        # start() so construction never touches the network.
        try:
            import confluent_kafka  # type: ignore  # noqa: F401

            self.degraded = False
            log.info("KafkaConsumerRuntime: confluent_kafka available -> live mode armed")
        except Exception:
            self.degraded = True
            log.warning("KafkaConsumerRuntime: confluent_kafka absent -> degraded no-op (Step 10)")

    # ------------------------------------------------------------------- start
    def start(self) -> bool:
        """Start the background consumer thread. Returns ``True`` if live.

        In degraded mode this is a clean no-op (returns ``False``) so callers can
        safely invoke it unconditionally; delivery then happens synchronously via
        the in-memory fallback.
        """
        if self.degraded:
            log.info("KafkaConsumerRuntime.start: degraded -> no consumer thread (Step 10)")
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        try:
            from confluent_kafka import Consumer  # type: ignore

            self._consumer = Consumer(
                {
                    "bootstrap.servers": self.bus.brokers,
                    "group.id": self.group_id,
                    "auto.offset.reset": "latest",
                    "enable.auto.commit": True,
                }
            )
            if self.subscriptions:
                self._consumer.subscribe(list(self.subscriptions.keys()))
        except Exception:
            # Any failure spinning up the real consumer degrades cleanly.
            self.degraded = True
            self._consumer = None
            log.exception("KafkaConsumerRuntime.start failed -> degraded no-op (Step 10)")
            return False

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="dm-kafka-consumer", daemon=True
        )
        self._thread.start()
        log.info("KafkaConsumerRuntime live; consuming %s", list(self.subscriptions))
        return True

    def _run(self) -> None:  # pragma: no cover - requires a live broker
        """Consumer poll loop (live mode only; never exercised in tests)."""
        assert self._consumer is not None
        while not self._stop.is_set():
            try:
                rec = self._consumer.poll(self.poll_timeout)
            except Exception:
                log.exception("consumer.poll raised; degrading")
                self.degraded = True
                break
            if rec is None:
                continue
            if rec.error():
                log.warning("consumer record error: %s", rec.error())
                continue
            self._dispatch(rec.topic(), rec.value())
        log.info("KafkaConsumerRuntime poll loop exited")

    def _dispatch(self, topic: str, raw: bytes | None) -> None:
        """Decode a record and invoke the matching callback (defensive)."""
        cb = self.subscriptions.get(topic)
        if cb is None:
            return
        try:
            import json

            data = json.loads((raw or b"{}").decode("utf-8"))
            msg = Message.from_dict(data) if hasattr(Message, "from_dict") else None
            if msg is None:  # pragma: no cover - depends on contracts API
                return
            cb(msg)
        except Exception:  # a bad record / failing callback must not kill the loop
            log.exception("dispatch on topic %s failed (continuing, Step 10)", topic)

    # -------------------------------------------------------------------- stop
    def stop(self, timeout: float = 5.0) -> None:
        """Signal the loop to exit and close the consumer (idempotent)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        if self._consumer is not None:
            try:
                self._consumer.close()
            except Exception:  # pragma: no cover - best-effort close
                log.exception("consumer close failed")
            self._consumer = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
