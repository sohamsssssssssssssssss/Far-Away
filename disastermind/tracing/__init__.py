"""DisasterMind tracing / observability deepening (PRD Step 9/10).

Two complementary, stdlib-only, network-free building blocks:

* :mod:`~disastermind.tracing.spans` — an in-memory :class:`SpanRecorder` and the
  :func:`trace` helper (context-manager *and* decorator) that captures structured
  spans (name, injectable-clock start/end, ``incident_id``, attributes,
  parent/child nesting). When OpenTelemetry is importable a recorder can mirror
  closed spans to a real OTel tracer; otherwise it is the sole, fully-functional
  backend (graceful degradation, PRD Step 10).

* :mod:`~disastermind.tracing.collector` — a Tier-3, zero-authority
  :class:`TraceCollector` that subscribes to every :class:`Topic` and correlates
  the message stream *by ``incident_id``*, deriving a per-incident logical latency
  (first ``RAW_FEED`` -> last real ``DISPATCH``) from message ordering, never the
  wall clock.

:func:`build_agents` (in :mod:`~disastermind.tracing.build`) follows the uniform
module-factory contract but is **not** auto-wired into the DAG — tracing is opt-in
so it can never perturb the load-bearing chain (PRD Step 2).
"""
from __future__ import annotations

from .build import build_agents
from .collector import TraceCollector, all_topics
from .spans import (
    OTLP_ENDPOINT_ENV,
    Clock,
    Span,
    SpanRecorder,
    get_default_recorder,
    trace,
)

__all__ = [
    # spans
    "Clock",
    "Span",
    "SpanRecorder",
    "trace",
    "get_default_recorder",
    "OTLP_ENDPOINT_ENV",
    # collector
    "TraceCollector",
    "all_topics",
    # build
    "build_agents",
]
