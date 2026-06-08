"""Process runtime / supervisor (PRD Step 10).

:class:`ProcessRunner` is the long-lived process wrapper around the wired agent
DAG. It:

  * builds the system via :func:`disastermind.orchestration.build.build_system`
    (one bus, one logger, all tiers — defensively, skipping failed modules),
  * runs the :class:`~disastermind.orchestration.loop.CoordinationLoop`,
  * optionally drives a :class:`~disastermind.runtime.consumer.KafkaConsumerRuntime`
    for the real Kafka path (no-op in degraded mode),
  * installs a ``SIGINT`` handler for **graceful shutdown** so that on
    Ctrl-C the loop stops cleanly and field teams keep their last orders
    (PRD Step 10) rather than the process dying mid-cycle.

The runner is deterministic and network-free for tests: ``clock`` and ``sleep``
are injectable and :meth:`step` advances exactly one cycle without ever sleeping.
"""
from __future__ import annotations

import logging
import signal
import threading
from typing import Callable

from ..audit.decision_log import DecisionLogger
from ..core.bus import MessageBus
from ..core.config import Settings
from ..orchestration.build import build_system
from ..orchestration.loop import CoordinationLoop
from .consumer import KafkaConsumerRuntime

log = logging.getLogger("disastermind.runtime.runner")


class ProcessRunner:
    """Long-lived supervisor for the DisasterMind coordination loop (Step 10).

    Parameters
    ----------
    bus, logger, settings:
        Forwarded to :func:`build_system`; any may be ``None`` to use defaults
        (in-memory bus, null logger, env-derived :class:`Settings`).
    install_signals:
        When ``True`` (and running on the main thread), :meth:`start` installs a
        ``SIGINT`` handler so Ctrl-C performs a graceful shutdown. Disabled by
        default so it is safe to construct in tests / worker threads.
    consumer:
        Optional pre-built :class:`KafkaConsumerRuntime`; otherwise one is created
        from the bus (degraded no-op unless a healthy ``KafkaBus`` was supplied).
    """

    def __init__(
        self,
        bus: MessageBus | None = None,
        logger: DecisionLogger | None = None,
        settings: Settings | None = None,
        install_signals: bool = False,
        consumer: KafkaConsumerRuntime | None = None,
    ) -> None:
        self.loop: CoordinationLoop = build_system(bus, logger, settings)
        self.settings = self.loop.settings
        self.install_signals = install_signals
        self.consumer = consumer or KafkaConsumerRuntime(self.loop.bus)
        self._running = False
        self._prev_sigint = None
        self._shutdown = threading.Event()

    # ----------------------------------------------------------------- helpers
    @property
    def degraded_modules(self) -> list[str]:
        """Modules that failed to load (graceful degradation, Step 10)."""
        return self.loop.degraded_modules

    @property
    def cycle(self) -> int:
        return self.loop.cycle

    # -------------------------------------------------------------------- step
    def step(self, now_epoch: float | None = None) -> int:
        """Advance exactly one coordination cycle. Never sleeps.

        Returns the (1-based) cycle number after advancing.
        """
        return self.loop.run_once(now_epoch)

    # -------------------------------------------------------------- lifecycle
    def _install_sigint(self) -> None:
        """Install a graceful-shutdown SIGINT handler (main-thread only)."""
        if not self.install_signals:
            return
        if threading.current_thread() is not threading.main_thread():
            log.warning("ProcessRunner: not on main thread; skipping SIGINT handler")
            return

        def _handler(signum, frame):  # pragma: no cover - delivered via real signal
            log.info("SIGINT received — graceful shutdown; field teams keep last orders")
            self.stop()

        try:
            self._prev_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError):  # pragma: no cover - non-main / unsupported
            log.warning("ProcessRunner: could not install SIGINT handler")

    def _restore_sigint(self) -> None:
        if self._prev_sigint is None:
            return
        try:
            signal.signal(signal.SIGINT, self._prev_sigint)
        except (ValueError, OSError):  # pragma: no cover
            pass
        self._prev_sigint = None

    def start(
        self,
        max_cycles: int | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> int:
        """Run the coordination loop until stopped or ``max_cycles`` is reached.

        ``clock``/``sleep`` are injectable for deterministic tests (pass a no-op
        ``sleep`` to assert no real sleeping). Starts the Kafka consumer runtime
        first (a clean no-op when degraded). Returns the cycle count executed.
        """
        self._running = True
        self._shutdown.clear()
        self._install_sigint()
        # Live Kafka consumer (no-op + degraded flag when no broker / lib).
        self.consumer.start()
        try:
            executed = self.loop.run(max_cycles=max_cycles, clock=clock, sleep=sleep)
        finally:
            self._running = False
            self._restore_sigint()
        return executed

    def stop(self) -> None:
        """Request a graceful shutdown of the loop and consumer (idempotent).

        Field teams keep their last orders: we only stop *issuing* new cycles —
        nothing rolls back already-published dispatch orders (PRD Step 10).
        """
        self._shutdown.set()
        self.loop.stop()
        try:
            self.consumer.stop()
        except Exception:  # pragma: no cover - best-effort
            log.exception("consumer stop failed during shutdown")

    @property
    def running(self) -> bool:
        return self._running
