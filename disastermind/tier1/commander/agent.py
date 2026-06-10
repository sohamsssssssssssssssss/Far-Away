"""Tier-1 Commander agent (PRD Step 7).

The Commander is the single human-in-the-loop gatekeeper. It subscribes to
``Topic.FIELD_ORDER`` and reviews every order from the Tier-2 field-coordination
agent against the Autonomy Threshold Matrix (:mod:`.matrix`):

  * Within autonomous authority  -> publish ``Topic.DISPATCH`` immediately (no hold).
  * Requires human escalation     -> build an :class:`~disastermind.models.domain.EscalationReport`,
    publish ``Topic.ESCALATION`` to the human dashboard, and register a *pending*
    decision with a configurable timeout (``settings.escalation_timeout_seconds``,
    default 300 s).

Timeout handling is **event-driven**, never blocking: the coordination loop calls
:meth:`resolve_pending(now_epoch)` each cycle. On timeout the order AUTO-EXECUTES
(publishes ``Topic.DISPATCH``) UNLESS its trigger is in
:data:`~disastermind.core.contracts.HUMAN_ONLY_TRIGGERS`, in which case the agent
never acts alone — it simply keeps waiting for a human.

Humans resolve a pending escalation out-of-band by calling :meth:`approve` /
:meth:`reject` (the dashboard wires these to inbound approval messages). All
egress goes through :meth:`BaseAgent.emit`, so every dispatch/escalation is
audit-logged before publication (PRD Step 9).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.config import Settings
from ...core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from ...models.domain import EscalationReport
from .matrix import (
    AutonomyRule,
    Decision,
    build_matrix,
    classify,
    order_priority,
)


@dataclass
class PendingEscalation:
    """An escalated order awaiting a human decision or timeout (PRD Step 7)."""

    report_id: str
    decision: Decision
    order: dict[str, Any]
    incident_id: str | None
    module: Module
    created_epoch: float
    deadline_epoch: float  # created + timeout (informational for human-only)
    reasoning: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | approved | rejected | auto_executed | expired

    def is_due(self, now_epoch: float) -> bool:
        return now_epoch >= self.deadline_epoch


class CommanderAgent(BaseAgent):
    """Reviews field orders against the autonomy matrix (PRD Step 7)."""

    tier = Tier.COMMANDER
    decision_authority = True  # Tier 1 holds final review authority

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        settings: Settings | None = None,
        name: str = "commander",
    ) -> None:
        self.settings = settings or Settings()
        self.default_timeout = int(self.settings.escalation_timeout_seconds)
        self.matrix: dict[EscalationTrigger, AutonomyRule] = build_matrix(self.default_timeout)
        self.pending: dict[str, PendingEscalation] = {}
        # audit-friendly counters
        self.stats = {
            "dispatched": 0,
            "escalated": 0,
            "auto_executed": 0,
            "approved": 0,
            "rejected": 0,
        }
        super().__init__(
            name=name,
            bus=bus,
            logger=logger,
            subscriptions=[Topic.FIELD_ORDER],
        )

    # ------------------------------------------------------------------ reactive
    def handle(self, message: Message) -> list[Message]:
        """Review each order in an inbound FIELD_ORDER payload (PRD Step 7)."""
        if message.topic != Topic.FIELD_ORDER:
            return []
        payload = message.payload or {}
        incident_id = message.incident_id or payload.get("incident_id")
        module = message.module if isinstance(message.module, Module) else Module.ALL
        orders = payload.get("orders") or []
        payload_escalation = payload.get("escalation")

        out: list[Message] = []
        for order in orders:
            decision = classify(
                order,
                self.matrix,
                self.default_timeout,
                escalation=payload_escalation,
            )
            if decision.autonomous:
                out.append(self._build_dispatch(order, decision, incident_id, module))
                self.stats["dispatched"] += 1
            else:
                out.append(
                    self._register_and_escalate(order, decision, incident_id, module)
                )
                self.stats["escalated"] += 1
        return out

    # ------------------------------------------------------------------ dispatch
    def _build_dispatch(
        self,
        order: dict[str, Any],
        decision: Decision,
        incident_id: str | None,
        module: Module,
        via: str = "autonomous",
    ) -> Message:
        """Construct a Topic.DISPATCH message for an approved/autonomous order."""
        team = order.get("team_id", "unassigned")
        site = order.get("site")
        reason = order.get("reason", "")
        body = (
            f"DISPATCH {team} -> {site}: {reason}".strip()
            if site is not None
            else f"DISPATCH {team}: {reason}".strip()
        )
        reasoning = [f"commander review: {via}"] + list(decision.reasoning)
        return Message(
            sender=self.name,
            recipient="dispatch",
            type=MessageType.INSTRUCTION,
            priority=order_priority(order),
            topic=Topic.DISPATCH,
            incident_id=incident_id,
            module=module,
            reasoning=reasoning,
            payload={
                "channel": order.get("channel", "field_radio"),
                "recipients": order.get("recipients", [team]),
                "body": body,
                "order": order,
                "via": via,
            },
        )

    # ---------------------------------------------------------------- escalation
    def _register_and_escalate(
        self,
        order: dict[str, Any],
        decision: Decision,
        incident_id: str | None,
        module: Module,
    ) -> Message:
        """Persist a pending escalation and emit an ESCALATION to the dashboard."""
        report_id = f"esc-{uuid.uuid4().hex[:12]}"
        now = self._now()
        timeout = decision.timeout_seconds or self.default_timeout
        pending = PendingEscalation(
            report_id=report_id,
            decision=decision,
            order=order,
            incident_id=incident_id,
            module=module,
            created_epoch=now,
            deadline_epoch=now + timeout,
            reasoning=list(decision.reasoning),
        )
        self.pending[report_id] = pending

        trig = decision.trigger
        summary = self._summary_for(order, decision)
        recommended = (
            "Hold for human authorisation (NEVER auto-execute)."
            if decision.human_only
            else f"Auto-execute in {timeout}s unless a human responds."
        )
        report = EscalationReport(
            report_id=report_id,
            trigger=trig.value if trig else "unknown",
            summary=summary,
            recommended_action=recommended,
            timeout_seconds=timeout,
            human_only=decision.human_only,
            supporting={
                "order": order,
                "incident_id": incident_id,
                "deadline_epoch": pending.deadline_epoch,
            },
        )
        return Message(
            sender=self.name,
            recipient="human_dashboard",
            type=MessageType.ESCALATION,
            priority=Priority.CRITICAL,
            topic=Topic.ESCALATION,
            incident_id=incident_id,
            module=module,
            escalation_trigger=trig,
            reasoning=list(decision.reasoning),
            payload={
                "kind": "escalation",
                "report_id": report_id,
                "report": _asdict(report),
                "human_only": decision.human_only,
                "timeout_seconds": timeout,
            },
        )

    @staticmethod
    def _summary_for(order: dict[str, Any], decision: Decision) -> str:
        team = order.get("team_id", "?")
        site = order.get("site", "?")
        reason = order.get("reason", "")
        trig = decision.trigger.value if decision.trigger else "escalation"
        return f"[{trig}] order for team {team} -> {site}: {reason}".strip()

    # ------------------------------------------------------------ timeout loop
    def resolve_pending(self, now_epoch: float | None = None) -> list[Message]:
        """Event-driven timeout sweep — call once per coordination cycle.

        Does NOT sleep. For each due pending escalation:
          * human-only trigger  -> keep waiting (never auto-act); emit nothing.
          * otherwise            -> AUTO-EXECUTE: publish Topic.DISPATCH.

        Returns the messages it emitted (also emitted via the bus so callers may
        ignore the return value). PRD Step 7.
        """
        now = self._now() if now_epoch is None else now_epoch
        emitted: list[Message] = []
        for report_id, pending in list(self.pending.items()):
            if pending.status != "pending" or not pending.is_due(now):
                continue
            if pending.decision.human_only:
                # Never auto-execute. The escalation stays open until a human
                # acts; we leave it pending (do not expire it).
                continue
            pending.status = "auto_executed"
            self.stats["auto_executed"] += 1
            msg = self._build_dispatch(
                pending.order,
                pending.decision,
                pending.incident_id,
                pending.module,
                via="auto_execute_on_timeout",
            )
            msg.reasoning = [
                f"escalation {report_id} timed out after {pending.decision.timeout_seconds}s "
                f"with no human response -> auto-executing (not human-only)"
            ] + msg.reasoning
            self.pending.pop(report_id, None)
            self.emit(msg)
            emitted.append(msg)
        return emitted

    def tick(self) -> list[Message]:
        """Periodic hook (PRD Step 10): drive the timeout sweep each cycle."""
        # emit() already happened inside resolve_pending; return [] so the
        # BaseAgent run_tick loop does not double-emit.
        self.resolve_pending(self._now())
        return []

    # --------------------------------------------------------- human responses
    def approve(self, report_id: str, approver: str = "human") -> list[Message]:
        """Human approves a pending escalation -> dispatch now (PRD Step 7)."""
        pending = self.pending.get(report_id)
        if pending is None or pending.status != "pending":
            return []
        pending.status = "approved"
        self.stats["approved"] += 1
        msg = self._build_dispatch(
            pending.order,
            pending.decision,
            pending.incident_id,
            pending.module,
            via=f"human_approved:{approver}",
        )
        self.pending.pop(report_id, None)
        self.emit(msg)
        return [msg]

    def reject(self, report_id: str, approver: str = "human", note: str = "") -> list[Message]:
        """Human rejects a pending escalation -> no dispatch; emit an ACK record."""
        pending = self.pending.get(report_id)
        if pending is None or pending.status != "pending":
            return []
        pending.status = "rejected"
        self.stats["rejected"] += 1
        ack = Message(
            sender=self.name,
            recipient="human_dashboard",
            type=MessageType.ACK,
            priority=Priority.HIGH,
            topic=Topic.ESCALATION,
            incident_id=pending.incident_id,
            module=pending.module,
            escalation_trigger=pending.decision.trigger,
            reasoning=[f"escalation {report_id} REJECTED by {approver}: {note}".strip()],
            payload={
                "kind": "escalation_rejected",
                "report_id": report_id,
                "approver": approver,
                "note": note,
            },
        )
        self.pending.pop(report_id, None)
        self.emit(ack)
        return [ack]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _now() -> float:
        return time.time()

    def pending_reports(self) -> list[dict[str, Any]]:
        """Snapshot of open escalations (for the human dashboard / tests)."""
        return [
            {
                "report_id": p.report_id,
                "trigger": p.decision.trigger.value if p.decision.trigger else None,
                "human_only": p.decision.human_only,
                "deadline_epoch": p.deadline_epoch,
                "status": p.status,
                "incident_id": p.incident_id,
            }
            for p in self.pending.values()
        ]


def _asdict(obj: Any) -> dict[str, Any]:
    """Serialise a dataclass to a JSON-able dict (lazy import-free)."""
    from dataclasses import asdict as _da

    return _da(obj)
