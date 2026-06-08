"""State-persistence integration (PRD Step 9 — Decision Logging & State).

Bridges the live message bus to the durable :mod:`disastermind.storage` layer:
the :class:`StatePersistor` subscribes to every topic and writes the message
stream *through* the four storage repositories (audit index, telemetry
hypertable, spatial asset state) while emitting nothing and holding no decision
authority. Offline by default (``Storage.in_memory()``) — no network at import
or in any test (PRD Step 10 graceful degradation).
"""
from __future__ import annotations

from .persistor import StatePersistor, all_topics

__all__ = ["StatePersistor", "all_topics"]
