"""In-memory span recording with optional OpenTelemetry export (PRD Step 9/10).

This is the tracing half of DisasterMind's observability deepening. It lets any
code wrap a unit of work in a :func:`trace` and capture a structured
:class:`Span` — name, start/end ticks, ``incident_id``, free-form attributes and
parent/child nesting — without pulling in a heavyweight tracing SDK.

Design constraints (PRD HARD RULE 2 — stdlib only, deterministic, no network):
  * **Injectable clock.** :func:`trace` reads time from a caller-supplied
    ``clock`` callable (or the recorder's default). Tests pass a monotone
    counter so assertions never touch wall-clock time.
  * **Parent nesting.** Spans opened inside an active span automatically record
    the enclosing span's id as ``parent_id`` (tracked per-thread), so a recorded
    trace forms a tree. Nesting is restored correctly even if a span raises.
  * **Lazy OpenTelemetry.** When the ``opentelemetry`` SDK is importable and an
    exporter is wired (:meth:`SpanRecorder.enable_otel`), finished spans are also
    mirrored to a real OTel tracer. Otherwise the in-memory recorder is the sole,
    fully-functional backend (graceful degradation, PRD Step 10). The import is
    performed lazily inside a method and wrapped in ``try/except`` so neither the
    package import nor any default test path requires the SDK or a collector.

The :func:`trace` helper works both as a context manager::

    with trace("predict", recorder=rec, incident_id="EQ-1", clock=clk) as span:
        span.set("model", "xgboost")

and as a decorator::

    @trace("predict", recorder=rec)
    def predict(...): ...
"""
from __future__ import annotations

import functools
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("disastermind.tracing.spans")

#: A clock returns a comparable, monotone "tick" (float epoch seconds in prod,
#: or an injected integer counter in tests). We never compare ticks to wall time.
Clock = Callable[[], float]


@dataclass
class Span:
    """A single recorded unit of work (PRD Step 9 decision tracing).

    ``start``/``end`` are clock ticks (see :data:`Clock`), so ``duration`` is a
    tick delta — meaningful relative to the same clock, never asserted against
    real time. ``parent_id`` is ``None`` for a root span.
    """

    name: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    incident_id: str | None = None
    start: float = 0.0
    end: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"

    # ----------------------------------------------------------- mutation API
    def set(self, key: str, value: Any) -> "Span":
        """Attach/overwrite one attribute; returns self for chaining."""
        self.attributes[key] = value
        return self

    def update(self, **attrs: Any) -> "Span":
        """Attach several attributes at once."""
        self.attributes.update(attrs)
        return self

    # --------------------------------------------------------------- queries
    @property
    def duration(self) -> float | None:
        """End tick minus start tick, or ``None`` while the span is open."""
        if self.end is None:
            return None
        return self.end - self.start

    @property
    def is_closed(self) -> bool:
        return self.end is not None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view (mirrors the codebase's ``to_dict`` style)."""
        return {
            "name": self.name,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "incident_id": self.incident_id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "attributes": dict(self.attributes),
            "status": self.status,
        }


class SpanRecorder:
    """Thread-safe in-memory store of :class:`Span` objects.

    Holds the default clock for spans opened against it and tracks the active
    span per-thread so nested :func:`trace` blocks set ``parent_id`` correctly.
    When OpenTelemetry is enabled (and importable), closed spans are also
    mirrored to an OTel tracer; the in-memory list remains the source of truth.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        # Default clock: a deterministic monotone counter so the recorder is
        # usable (and testable) with NO real-time dependency. Callers in
        # production may inject ``time.monotonic``.
        self._tick = 0
        self.clock: Clock = clock or self._default_clock
        self.spans: list[Span] = []
        self._lock = threading.Lock()
        self._stack = threading.local()  # per-thread list[Span] of open spans
        # OTel is opt-in and lazily resolved; None means "in-memory only".
        self._otel_tracer: Any | None = None

    # ------------------------------------------------------------ default clock
    def _default_clock(self) -> float:
        """Monotone integer tick (no wall-clock dependency)."""
        self._tick += 1
        return float(self._tick)

    # --------------------------------------------------------------- stack mgmt
    def _open_stack(self) -> list[Span]:
        stack = getattr(self._stack, "value", None)
        if stack is None:
            stack = []
            self._stack.value = stack
        return stack

    @property
    def current(self) -> Span | None:
        """The innermost open span on this thread, or ``None``."""
        stack = self._open_stack()
        return stack[-1] if stack else None

    # --------------------------------------------------------------- lifecycle
    def start_span(
        self,
        name: str,
        *,
        incident_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        clock: Clock | None = None,
    ) -> Span:
        """Open and register a new span as a child of the current open span.

        ``incident_id`` defaults to the enclosing span's incident so a nested
        trace inherits its incident correlation (PRD Step 9). Pass an explicit
        ``clock`` to override the recorder's default for this span only.
        """
        tick = (clock or self.clock)()
        parent = self.current
        span = Span(
            name=name,
            parent_id=parent.span_id if parent is not None else None,
            incident_id=incident_id if incident_id is not None
            else (parent.incident_id if parent is not None else None),
            start=tick,
            attributes=dict(attributes or {}),
        )
        with self._lock:
            self.spans.append(span)
        self._open_stack().append(span)
        return span

    def end_span(
        self,
        span: Span,
        *,
        status: str = "ok",
        clock: Clock | None = None,
    ) -> Span:
        """Close ``span``, pop it off the per-thread stack, mirror to OTel."""
        span.end = (clock or self.clock)()
        span.status = status
        stack = self._open_stack()
        # Pop defensively: the span should be on top, but tolerate disorder so a
        # failure in one span never corrupts the whole stack (PRD Step 10).
        if span in stack:
            # remove the exact span (and anything left above it after an error)
            while stack and stack[-1] is not span:
                stack.pop()
            if stack:
                stack.pop()
        self._export_otel(span)
        return span

    # ------------------------------------------------------------------ queries
    def roots(self) -> list[Span]:
        """All recorded spans with no parent (the trace forest roots)."""
        return [s for s in self.spans if s.parent_id is None]

    def children_of(self, span_id: str) -> list[Span]:
        """Direct children of the given span id, in record order."""
        return [s for s in self.spans if s.parent_id == span_id]

    def by_incident(self, incident_id: str) -> list[Span]:
        """All recorded spans correlated to one incident (PRD Step 9)."""
        return [s for s in self.spans if s.incident_id == incident_id]

    def snapshot(self) -> list[dict[str, Any]]:
        """JSON-serialisable view of every recorded span, in record order."""
        with self._lock:
            return [s.to_dict() for s in self.spans]

    def reset(self) -> None:
        """Drop all recorded spans and clear this thread's open-span stack."""
        with self._lock:
            self.spans.clear()
        self._stack.value = []

    # --------------------------------------------------------------- OTel (lazy)
    def enable_otel(self, tracer: Any | None = None) -> bool:
        """Wire optional OpenTelemetry export; return True iff OTel is active.

        With ``tracer`` provided we use it directly. Otherwise we lazily import
        the SDK and acquire the global tracer. The import is wrapped in
        ``try/except`` so a missing SDK degrades silently to in-memory-only
        recording (PRD HARD RULE 2 / Step 10). No collector/network is contacted
        here — exporter configuration is the caller's responsibility.
        """
        if tracer is not None:
            self._otel_tracer = tracer
            return True
        try:  # pragma: no cover - exercised only when opentelemetry is installed
            from opentelemetry import trace as otel_trace  # type: ignore

            self._otel_tracer = otel_trace.get_tracer("disastermind.tracing")
            return True
        except Exception:
            log.info("opentelemetry unavailable; tracing stays in-memory only")
            self._otel_tracer = None
            return False

    @property
    def otel_enabled(self) -> bool:
        return self._otel_tracer is not None

    def _export_otel(self, span: Span) -> None:
        """Mirror a closed span to the OTel tracer if one is wired."""
        tracer = self._otel_tracer
        if tracer is None:
            return
        try:  # pragma: no cover - only with a real/mock OTel tracer present
            otel_span = tracer.start_span(span.name)
            for key, value in span.attributes.items():
                otel_span.set_attribute(key, value)
            if span.incident_id is not None:
                otel_span.set_attribute("incident_id", span.incident_id)
            otel_span.end()
        except Exception:
            log.exception("opentelemetry export failed for span %s", span.span_id)


#: Process-wide default recorder, used when :func:`trace` is called without an
#: explicit ``recorder=``. Tests should pass their own recorder for isolation.
_DEFAULT_RECORDER = SpanRecorder()


def get_default_recorder() -> SpanRecorder:
    """Return the process-wide default :class:`SpanRecorder`."""
    return _DEFAULT_RECORDER


class _TraceContext:
    """Context-manager / decorator returned by :func:`trace`.

    As a context manager it yields the live :class:`Span`; as a decorator it
    opens a fresh span around each call (re-entrant — safe to reuse).
    """

    def __init__(
        self,
        name: str,
        *,
        recorder: SpanRecorder | None,
        incident_id: str | None,
        clock: Clock | None,
        attributes: dict[str, Any],
    ) -> None:
        self.name = name
        self.recorder = recorder or _DEFAULT_RECORDER
        self.incident_id = incident_id
        self.clock = clock
        self.attributes = attributes
        self._span: Span | None = None

    # ----------------------------------------------------------- context mgr
    def __enter__(self) -> Span:
        self._span = self.recorder.start_span(
            self.name,
            incident_id=self.incident_id,
            attributes=self.attributes,
            clock=self.clock,
        )
        return self._span

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._span is not None:
            status = "ok" if exc_type is None else "error"
            if exc_type is not None:
                self._span.set("error", getattr(exc, "args", [repr(exc)]))
                self._span.set("error_type", getattr(exc_type, "__name__", str(exc_type)))
            self.recorder.end_span(self._span, status=status, clock=self.clock)
            self._span = None
        return False  # never suppress exceptions

    # --------------------------------------------------------------- decorator
    def __call__(self, func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # A fresh context per call so concurrent/re-entrant use is safe.
            ctx = _TraceContext(
                self.name,
                recorder=self.recorder,
                incident_id=self.incident_id,
                clock=self.clock,
                attributes=dict(self.attributes),
            )
            with ctx:
                return func(*args, **kwargs)

        return wrapper


def trace(
    name: str,
    *,
    recorder: SpanRecorder | None = None,
    incident_id: str | None = None,
    clock: Clock | None = None,
    **attrs: Any,
) -> _TraceContext:
    """Open a span named ``name`` (PRD Step 9 decision tracing).

    Usable as a context manager *or* a decorator (see module docstring). Extra
    keyword arguments become span attributes. ``recorder`` defaults to the
    process-wide recorder; tests should pass their own for isolation. ``clock``
    overrides the recorder's default clock for this span (and is reused on close
    so start/end share a single tick source — deterministic in tests).
    """
    return _TraceContext(
        name,
        recorder=recorder,
        incident_id=incident_id,
        clock=clock,
        attributes=dict(attrs),
    )
