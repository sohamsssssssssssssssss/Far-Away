"""Pure dashboard service layer (PRD Step 7 dashboard + Step 10 refresh).

This module is the *transport-free* core of the human Commander Dashboard. It
holds no web framework and no I/O of its own: it observes the live
:class:`~disastermind.core.bus.MessageBus` history and delegates every
escalation action to the real :class:`~disastermind.tier1.commander.agent.CommanderAgent`
(its ``pending`` registry plus :meth:`approve` / :meth:`reject` / :meth:`pending_reports`).

Splitting logic from transport (PRD Step 7) means the same
:class:`DashboardService` powers the FastAPI app in :mod:`disastermind.api.app`
*and* is unit-testable with the standard library alone — no FastAPI needed.

The service is intentionally read-mostly over the bus: it never publishes
directly, it asks the Commander to act so that every dispatch/ACK still flows
through :meth:`BaseAgent.emit` and stays audit-logged (PRD Step 9).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable

from ..core.bus import MessageBus
from ..core.contracts import Message, MessageType, Topic

# New topic-string constant owned by THIS package (we never edit core/contracts.py).
# The WebSocket /ws endpoint fans out every bus message it observes; consumers may
# filter on ``Message.topic`` themselves.
WS_STREAM = "api.ws_stream"


def _msg_to_dict(message: Message) -> dict[str, Any]:
    """JSON-able view of a :class:`Message` for the dashboard wire format."""
    return message.to_dict()


@dataclass
class DashboardService:
    """Transport-free backend for the Commander Dashboard (PRD Step 7).

    Parameters
    ----------
    bus:
        The shared in-process / Kafka bus. We read its ``history`` ring buffer
        for incident & topic views and subscribe to it to stream live updates.
    commander:
        The live :class:`CommanderAgent`. We delegate escalation queries and
        approve/reject actions to it directly (no re-implementation of policy).
    history_limit:
        Default cap for :meth:`recent` when the caller passes no limit.
    """

    bus: MessageBus
    commander: Any  # CommanderAgent — typed as Any to avoid an import cycle
    history_limit: int = 100
    _subscribers: list[Callable[[dict[str, Any]], None]] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)
    _streaming: bool = False

    # ------------------------------------------------------------------ health
    def health(self) -> dict[str, Any]:
        """Liveness + a quick snapshot of how busy the system is (PRD Step 10)."""
        return {
            "status": "ok",
            "commander": getattr(self.commander, "name", None),
            "messages_seen": len(getattr(self.bus, "history", [])),
            "pending_escalations": len(self.list_escalations()),
        }

    # ------------------------------------------------------------------- topics
    def topic_counts(self) -> dict[str, int]:
        """Message volume per topic across the bus history (dashboard tiles)."""
        counts: dict[str, int] = {}
        for m in getattr(self.bus, "history", []):
            counts[m.topic] = counts.get(m.topic, 0) + 1
        return counts

    # ---------------------------------------------------------------- incidents
    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Most-recent bus messages, newest last, JSON-able (PRD Step 7 feed).

        ``limit`` defaults to :attr:`history_limit`; non-positive values return
        an empty list.
        """
        n = self.history_limit if limit is None else int(limit)
        history = list(getattr(self.bus, "history", []))
        if n <= 0:
            return []
        return [_msg_to_dict(m) for m in history[-n:]]

    def incidents(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Distinct active incidents derived from the bus history.

        Groups messages by ``incident_id`` and reports the latest activity,
        message count and dominant module so the dashboard can list "what is
        happening right now" (PRD Step 7).
        """
        agg: dict[str, dict[str, Any]] = {}
        for m in getattr(self.bus, "history", []):
            iid = m.incident_id
            if not iid:
                continue
            entry = agg.get(iid)
            if entry is None:
                entry = {
                    "incident_id": iid,
                    "module": m.module.value,
                    "message_count": 0,
                    "topics": set(),
                    "last_timestamp": m.timestamp,
                    "last_topic": m.topic,
                }
                agg[iid] = entry
            entry["message_count"] += 1
            entry["topics"].add(m.topic)
            entry["last_timestamp"] = m.timestamp
            entry["last_topic"] = m.topic
        rows = []
        for entry in agg.values():
            row = dict(entry)
            row["topics"] = sorted(entry["topics"])
            rows.append(row)
        rows.sort(key=lambda r: r["last_timestamp"])
        if limit is not None:
            n = int(limit)
            rows = rows[-n:] if n > 0 else []
        return rows

    # -------------------------------------------------------------- escalations
    def list_escalations(self) -> list[dict[str, Any]]:
        """Open escalations awaiting a human decision (delegates to commander)."""
        reporter = getattr(self.commander, "pending_reports", None)
        if callable(reporter):
            return list(reporter())
        return []

    def get_escalation(self, report_id: str) -> dict[str, Any] | None:
        """A single pending escalation by id, or ``None`` if not pending."""
        for row in self.list_escalations():
            if row.get("report_id") == report_id:
                return row
        return None

    def approve(self, report_id: str, approver: str = "human") -> dict[str, Any]:
        """Human approves an escalation -> commander dispatches (PRD Step 7)."""
        emitted = self.commander.approve(report_id, approver=approver)
        ok = bool(emitted)
        return {
            "report_id": report_id,
            "action": "approve",
            "approver": approver,
            "ok": ok,
            "dispatched": [_msg_to_dict(m) for m in emitted],
        }

    def reject(
        self, report_id: str, approver: str = "human", note: str = ""
    ) -> dict[str, Any]:
        """Human rejects an escalation -> commander emits a rejection ACK."""
        emitted = self.commander.reject(report_id, approver=approver, note=note)
        ok = bool(emitted)
        return {
            "report_id": report_id,
            "action": "reject",
            "approver": approver,
            "note": note,
            "ok": ok,
            "acks": [_msg_to_dict(m) for m in emitted],
        }

    # ----------------------------------------------------------- live streaming
    def start_streaming(self, subscriber: str = "api.dashboard") -> None:
        """Subscribe to every known topic so :meth:`add_listener` callbacks fire.

        PRD Step 10 (WebSocket refresh): the dashboard pushes new bus messages to
        connected clients. We attach one bus subscription per well-known topic;
        each delivered message is fanned out to registered listeners as a dict.
        Idempotent — a second call is a no-op.
        """
        if self._streaming:
            return
        self._streaming = True
        for topic in self._known_topics():
            self.bus.subscribe(topic, subscriber, self._on_bus_message)

    def _on_bus_message(self, message: Message) -> None:
        payload = _msg_to_dict(message)
        with self._lock:
            listeners = list(self._subscribers)
        for cb in listeners:
            try:
                cb(payload)
            except Exception:  # a slow/broken client must not stall the bus
                pass

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Register a callback invoked with each new bus message (as a dict).

        Returns an unsubscribe function. Used by the WebSocket endpoint to push
        live updates and by tests to assert streaming without any web stack.
        """
        with self._lock:
            self._subscribers.append(callback)

        def _remove() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _remove

    @staticmethod
    def _known_topics() -> list[str]:
        """All Topic constants plus this package's WS stream marker."""
        topics = [
            getattr(Topic, name)
            for name in dir(Topic)
            if not name.startswith("_") and isinstance(getattr(Topic, name), str)
        ]
        return topics
