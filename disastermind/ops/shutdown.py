"""Graceful shutdown handling (PRD Step 10 — field teams keep their last orders).

When the orchestrator receives ``SIGTERM`` (container stop / deploy) or
``SIGINT`` (operator Ctrl-C) it must NOT just vanish: in a live disaster the
field teams in the wilderness must keep their last dispatched orders, and any
in-flight state must be flushed. :class:`GracefulShutdown` lets the system
register ordered *drain callbacks* (persist state, flush the audit log, tell the
bus to stop accepting work, notify field apps) that are run exactly once when a
shutdown is requested.

Design goals
------------
* **Testable without signals.** The actual signal handler simply calls
  :meth:`GracefulShutdown.trigger`; tests drive ``trigger`` directly and never
  raise a real signal. ``install()`` (which calls ``signal.signal``) is opt-in
  and never invoked at import time.
* **Robust.** A callback that raises does not abort the rest of the drain — the
  remaining callbacks still run (we are shutting down; do as much cleanup as we
  can). Errors are collected and returned.
* **Idempotent.** A second trigger is a no-op (drains run once).
* **Inert by default.** Importing this module installs nothing.
"""
from __future__ import annotations

import logging
import signal
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

log = logging.getLogger("disastermind.ops.shutdown")

#: a drain callback takes no args and returns anything (return value ignored).
DrainCallback = Callable[[], Any]

#: signals we treat as "shut down gracefully" by default.
DEFAULT_SIGNALS: tuple[int, ...] = (signal.SIGTERM, signal.SIGINT)


@dataclass
class GracefulShutdown:
    """Registry of drain callbacks run once when shutdown is requested.

    Typical wiring::

        gs = GracefulShutdown()
        gs.register(loop.stop, name="stop-loop")
        gs.register(storage.flush, name="flush-state")
        gs.register(logger.flush, name="flush-audit")
        gs.install()            # arms SIGTERM/SIGINT (opt-in, real process only)
        ...
        # on SIGTERM the handler calls gs.trigger("SIGTERM") which drains in order

    Callbacks run in **registration order** (so you can persist state before you
    tell the bus to stop). Set ``reverse=True`` to drain LIFO instead.
    """

    reverse: bool = False
    _callbacks: list[tuple[str, DrainCallback]] = field(default_factory=list, init=False)
    triggered: bool = False
    reason: str | None = None
    #: (name, exception) for every callback that raised during the drain.
    errors: list[tuple[str, BaseException]] = field(default_factory=list, init=False)
    _previous_handlers: dict[int, Any] = field(default_factory=dict, init=False)

    # ----------------------------------------------------------- registration
    def register(self, callback: DrainCallback, *, name: str | None = None) -> DrainCallback:
        """Register a drain callback. Returns it so it can be used as a decorator."""
        if not callable(callback):
            raise TypeError("drain callback must be callable")
        label = name or getattr(callback, "__name__", repr(callback))
        self._callbacks.append((label, callback))
        return callback

    def unregister(self, callback: DrainCallback) -> bool:
        """Remove a previously-registered callback (by identity). True if removed."""
        for i, (_name, cb) in enumerate(self._callbacks):
            if cb is callback:
                del self._callbacks[i]
                return True
        return False

    @property
    def callbacks(self) -> list[str]:
        """Names of the registered drain callbacks, in registration order."""
        return [name for name, _cb in self._callbacks]

    # -------------------------------------------------------------- triggering
    def trigger(self, reason: str = "manual") -> bool:
        """Run every registered drain callback exactly once.

        ``reason`` is recorded for the operator (e.g. ``"SIGTERM"``). Returns
        ``True`` if this call performed the drain, ``False`` if shutdown had
        already been triggered (idempotent — drains never run twice).

        A callback that raises is logged and recorded in :attr:`errors`; the
        remaining callbacks STILL run. We are tearing down — best-effort cleanup
        beats aborting halfway.
        """
        if self.triggered:
            log.debug("shutdown already triggered (reason=%s); ignoring %s", self.reason, reason)
            return False
        self.triggered = True
        self.reason = reason
        log.info("graceful shutdown requested (reason=%s)", reason)

        order = list(reversed(self._callbacks)) if self.reverse else list(self._callbacks)
        for name, cb in order:
            try:
                cb()
            except BaseException as exc:  # noqa: BLE001 - drain must not abort
                self.errors.append((name, exc))
                log.exception("drain callback %s failed during shutdown (continuing)", name)
        log.info(
            "graceful shutdown drain complete (%d callback(s), %d error(s))",
            len(order),
            len(self.errors),
        )
        return True

    # --------------------------------------------------------- signal handling
    def _handler(self, signum: int, _frame: Any) -> None:
        """OS signal handler — names the signal and triggers the drain."""
        try:
            name = signal.Signals(signum).name
        except (ValueError, AttributeError):  # pragma: no cover - exotic platforms
            name = f"signal {signum}"
        self.trigger(name)

    def install(self, signals: Iterable[int] = DEFAULT_SIGNALS) -> None:
        """Arm OS signal handlers for ``signals`` (opt-in; real process only).

        Stores the previous handler for each signal so :meth:`uninstall` can
        restore it. NEVER called at import time and NEVER exercised in tests —
        tests drive :meth:`trigger` directly. Failing to set a handler (e.g. not
        on the main thread) is swallowed so install is safe to attempt anywhere.
        """
        for sig in signals:
            try:
                self._previous_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._handler)
            except (ValueError, OSError, RuntimeError) as exc:  # pragma: no cover
                log.warning("could not install handler for signal %s: %r", sig, exc)

    def uninstall(self) -> None:
        """Restore the signal handlers replaced by :meth:`install`."""
        for sig, prev in list(self._previous_handlers.items()):
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError, RuntimeError):  # pragma: no cover
                pass
        self._previous_handlers.clear()

    def snapshot(self) -> dict[str, Any]:
        """A JSON-friendly view of the shutdown state (for /healthz)."""
        return {
            "triggered": self.triggered,
            "reason": self.reason,
            "callbacks": self.callbacks,
            "errors": [(name, repr(exc)) for name, exc in self.errors],
        }


__all__ = ["GracefulShutdown", "DEFAULT_SIGNALS", "DrainCallback"]
