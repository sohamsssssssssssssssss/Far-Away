"""Production live-runtime package (PRD Step 9/10 deployment).

:class:`~disastermind.live.system.LiveSystem` wires the proven orchestration DAG
(:func:`disastermind.orchestration.build.build_system`) into a deployable runner
that selects a real-or-degraded message bus and attaches real-or-fallback
persistence — while defaulting to a fully **offline, in-memory** path so the
package imports and the whole test-suite runs with the Python standard library
only and **no services / no network** (HARD RULE 2).

Typical usage::

    from disastermind.live import LiveSystem

    sys = LiveSystem.build()          # offline, in-memory by default
    sys.run_once(now_epoch=1000.0)    # one deterministic coordination cycle
    print(sys.health())               # operator dashboard dict

Going live is opt-in via :class:`~disastermind.core.config.Settings`:
``DM_USE_KAFKA=1`` selects a :class:`~disastermind.core.bus.KafkaBus` (which
itself degrades to in-memory with no broker), and ``live=True`` plus configured
DSNs/URLs wires real PostGIS / TimescaleDB / Elasticsearch / MinIO persistence —
each repository still degrading to its own in-memory fallback if unreachable.

Run the supervisor with ``python -m disastermind.live``.
"""
from __future__ import annotations

from .ingest import poll_feed, poll_feeds
from .resilient import resilient_poll_feeds
from .system import LiveSystem

__all__ = ["LiveSystem", "poll_feeds", "poll_feed", "resilient_poll_feeds"]
