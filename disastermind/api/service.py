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

from collections import OrderedDict
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


def _flatten_doc(obj: Any) -> str:
    """Lower-cased flattened string of all scalar values (substring audit search).

    Mirrors :meth:`ElasticsearchAuditRepo._flatten` so the in-memory audit-search
    fallback in :class:`DashboardService` matches the durable repo's semantics.
    """
    parts: list[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            parts.append(_flatten_doc(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            parts.append(_flatten_doc(v))
    else:
        parts.append(str(obj))
    return " ".join(parts).lower()


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
    idempotency_cap: int = 512  # max distinct Idempotency-Keys remembered (LRU)
    _subscribers: list[Callable[[dict[str, Any]], None]] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)
    _streaming: bool = False
    # Idempotency-Key -> first recorded approve/reject result. Bounded LRU so a
    # flood of unique keys cannot grow memory without bound (PRD Step 10).
    _idem: "OrderedDict[str, dict[str, Any]]" = field(default_factory=OrderedDict)
    _idem_lock: Lock = field(default_factory=Lock)

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

    # --------------------------------------------------------------- idempotency
    def approve_idempotent(
        self, report_id: str, approver: str = "human", *, key: str | None = None
    ) -> dict[str, Any]:
        """:meth:`approve` guarded by an optional ``Idempotency-Key``.

        When ``key`` is supplied and was seen before, the FIRST recorded result is
        returned verbatim and the commander is **not** asked to act again (so a
        retried POST never double-dispatches). Without a key this is a plain
        :meth:`approve`.
        """
        return self._idempotent("approve", report_id, key, lambda: self.approve(report_id, approver=approver))

    def reject_idempotent(
        self,
        report_id: str,
        approver: str = "human",
        note: str = "",
        *,
        key: str | None = None,
    ) -> dict[str, Any]:
        """:meth:`reject` guarded by an optional ``Idempotency-Key`` (see above)."""
        return self._idempotent(
            "reject", report_id, key, lambda: self.reject(report_id, approver=approver, note=note)
        )

    def _idempotent(
        self,
        action: str,
        report_id: str,
        key: str | None,
        act: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if not key:
            return act()
        # Scope the key by action+report so the same key reused across distinct
        # operations can't alias to an unrelated cached result.
        cache_key = f"{action}:{report_id}:{key}"
        with self._idem_lock:
            cached = self._idem.get(cache_key)
            if cached is not None:
                self._idem.move_to_end(cache_key)
                # Flag the replay so callers/tests can tell it wasn't re-executed.
                return {**cached, "idempotent_replay": True}
        result = act()
        with self._idem_lock:
            # Re-check under lock: a racing request may have populated it first.
            existing = self._idem.get(cache_key)
            if existing is not None:
                self._idem.move_to_end(cache_key)
                return {**existing, "idempotent_replay": True}
            self._idem[cache_key] = result
            self._idem.move_to_end(cache_key)
            while len(self._idem) > max(1, self.idempotency_cap):
                self._idem.popitem(last=False)
        return result

    # ----------------------------------------------------------- history (store)
    def _bus_history_dicts(self) -> list[dict[str, Any]]:
        """All bus-history messages as dicts (oldest first), JSON-able."""
        return [_msg_to_dict(m) for m in getattr(self.bus, "history", [])]

    def history_incidents(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Incident roll-up for the *history* view.

        Identical roll-up shape to :meth:`incidents` (back-compat for the
        ``/history/incidents`` route), so the dashboard can reuse one renderer.
        """
        return self.incidents(limit=limit)

    def audit_search(
        self,
        text: str | None = None,
        *,
        start: str | None = None,
        end: str | None = None,
        size: int = 50,
    ) -> list[dict[str, Any]]:
        """Search the in-memory bus history by free text + ISO time range.

        Fallback used when no durable audit store is wired (the durable path lives
        in :mod:`disastermind.api.app`, which prefers the StatePersistor's
        :class:`ElasticsearchAuditRepo`). Mirrors that repo's matcher: a case-
        insensitive substring over the flattened record, plus an inclusive
        ``timestamp`` range.
        """
        needle = text.lower() if text else None
        out: list[dict[str, Any]] = []
        for doc in self._bus_history_dicts():
            if needle is not None and needle not in _flatten_doc(doc):
                continue
            ts = doc.get("timestamp")
            if start is not None and (ts is None or ts < start):
                continue
            if end is not None and (ts is None or ts > end):
                continue
            out.append(doc)
            if len(out) >= max(0, size):
                break
        return out

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
