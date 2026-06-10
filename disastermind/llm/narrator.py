"""EscalationNarrator — Tier-1 advisory brief generator (PRD Step 7).

PRD Step 7: "Escalation report generated (via LLM layer — see Group B)."

The Commander (Tier 1) publishes a machine-oriented ``Topic.ESCALATION`` message
when a decision exceeds autonomous authority. This agent subscribes to that
topic and produces a *rich human-readable escalation brief* for the commander
dashboard. The brief always contains five sections:

  1. SITUATION SUMMARY — what happened and what is being requested.
  2. WHY THIS EXCEEDED AUTONOMOUS AUTHORITY — the trigger + authority rationale.
  3. RECOMMENDED ACTION — what the human should do.
  4. KEY RISKS — the consequences of acting / not acting.
  5. DECISION DEADLINE — when the window closes (and whether auto-execution applies).

The agent is ADVISORY only: ``decision_authority = False``. It NEVER dispatches
and NEVER mutates the escalation; it only narrates. Output is published on the
package-local topic :data:`ESCALATION_NARRATIVE` (we never edit
``core/contracts.py`` per the build rules).

Text generation is delegated to an :class:`~disastermind.llm.client.LLMClient`.
By default that is the deterministic, network-free
:class:`~disastermind.llm.client.TemplateClient`; an
:class:`~disastermind.llm.client.AnthropicClient` (``claude-opus-4-8``) is used
only when an API key is configured. Either way the prompt itself is a complete,
well-structured brief, so the fallback is always usable (PRD Step 10).
"""
from __future__ import annotations

from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.config import Settings
from ..core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
    utcnow_iso,
)
from .client import LLMClient, make_client

#: Package-local topic for the rendered brief (NOT in core/contracts.py by rule).
ESCALATION_NARRATIVE = "tier1.escalation_narrative"

#: Plain-language rationale per trigger — drives the "WHY" section of the brief.
_TRIGGER_RATIONALE: dict[str, str] = {
    EscalationTrigger.CROSS_STATE_RESOURCE.value: (
        "moving resources across state lines requires inter-state authorisation "
        "outside any single agent's mandate"
    ),
    EscalationTrigger.MILITARY_ASSET.value: (
        "deploying military assets requires defence-ministry sign-off"
    ),
    EscalationTrigger.MASS_EVACUATION.value: (
        "a mandatory evacuation of more than 10,000 people carries population-scale "
        "legal and safety consequences"
    ),
    EscalationTrigger.REQUISITION_PRIVATE.value: (
        "requisitioning private infrastructure has legal and compensation implications"
    ),
    EscalationTrigger.MEDIA_BROADCAST.value: (
        "issuing a public media broadcast order shapes mass behaviour and must be "
        "human-authorised"
    ),
    EscalationTrigger.INTERNATIONAL_AID.value: (
        "requesting international aid is a sovereign diplomatic act"
    ),
    EscalationTrigger.STATE_OF_EMERGENCY.value: (
        "declaring a state of emergency is a constitutional executive power"
    ),
    EscalationTrigger.ARMED_FORCES_CIVIL.value: (
        "deploying armed forces in a civil situation requires the highest civil authority"
    ),
    EscalationTrigger.CRITICAL_NATIONAL_INFRA.value: (
        "acting on critical national infrastructure carries national-security weight"
    ),
}


class EscalationNarrator(BaseAgent):
    """Renders a human brief for every Tier-1 escalation (PRD Step 7).

    Subscribes : :data:`~disastermind.core.contracts.Topic.ESCALATION`.
    Produces   : a :class:`~disastermind.core.contracts.Message` on
                 :data:`ESCALATION_NARRATIVE`.
    """

    tier = Tier.COMMANDER
    decision_authority = False  # advisory only — never dispatches (spec)

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        settings: Settings | None = None,
        client: LLMClient | None = None,
        name: str = "llm.escalation_narrator",
    ) -> None:
        self.settings = settings or Settings()
        self.client = client or make_client(self.settings)
        super().__init__(
            name=name,
            bus=bus,
            logger=logger,
            subscriptions=[Topic.ESCALATION],
        )

    # ------------------------------------------------------------------ reactive
    def handle(self, message: Message) -> list[Message]:
        """Narrate one inbound ESCALATION message (PRD Step 7).

        Ignores anything that is not a fresh escalation (e.g. the Commander's
        rejection ACKs published on the same topic) so we never double-narrate.
        """
        if message.topic != Topic.ESCALATION:
            return []
        if message.type is not MessageType.ESCALATION:
            return []
        payload = message.payload or {}
        if payload.get("kind") not in (None, "escalation"):
            return []

        facts = self._extract_facts(message)
        prompt = self._build_prompt(facts)
        try:
            brief = self.client.generate(prompt)
        except Exception:  # the client must never take down the agent (PRD Step 10)
            brief = prompt
        if not brief:
            brief = prompt

        return [self._build_narrative(message, facts, brief, prompt)]

    # ------------------------------------------------------------------- helpers
    def _extract_facts(self, message: Message) -> dict[str, Any]:
        """Flatten the escalation envelope + report into a fact dict."""
        payload = message.payload or {}
        report = payload.get("report") or {}

        trigger = (
            message.escalation_trigger.value
            if isinstance(message.escalation_trigger, EscalationTrigger)
            else (report.get("trigger") or payload.get("trigger") or "unknown")
        )
        human_only = bool(
            payload.get("human_only", report.get("human_only", False))
        )
        timeout = int(
            payload.get("timeout_seconds", report.get("timeout_seconds", 0)) or 0
        )
        supporting = report.get("supporting") or {}
        return {
            "report_id": payload.get("report_id") or report.get("report_id") or "unknown",
            "trigger": trigger,
            "summary": report.get("summary") or "an escalated field order",
            "recommended_action": report.get("recommended_action")
            or self._default_recommendation(human_only, timeout),
            "human_only": human_only,
            "timeout_seconds": timeout,
            "incident_id": message.incident_id,
            "module": self._module_label(message.module),
            "priority": int(message.priority),
            "reasoning": list(message.reasoning or []),
            "order": supporting.get("order") or payload.get("order") or {},
            "deadline_epoch": supporting.get("deadline_epoch"),
        }

    @staticmethod
    def _module_label(module: Any) -> str:
        if isinstance(module, Module):
            return module.value
        return str(module) if module is not None else Module.ALL.value

    @staticmethod
    def _default_recommendation(human_only: bool, timeout: int) -> str:
        if human_only:
            return "Hold for human authorisation (NEVER auto-execute)."
        if timeout:
            return f"Auto-execute in {timeout}s unless a human responds."
        return "Await human decision."

    def _why(self, facts: dict[str, Any]) -> str:
        rationale = _TRIGGER_RATIONALE.get(
            facts["trigger"],
            "this decision falls outside the autonomous authority matrix",
        )
        gate = (
            "It is HUMAN-ONLY: the system will never act on it alone, even on timeout."
            if facts["human_only"]
            else "Absent a human response it will auto-execute when the deadline passes."
        )
        return f"Trigger '{facts['trigger']}' — {rationale}. {gate}"

    @staticmethod
    def _risks(facts: dict[str, Any]) -> list[str]:
        risks: list[str] = []
        if facts["human_only"]:
            risks.append(
                "Inaction stalls a critical response: this trigger never auto-executes."
            )
        else:
            risks.append(
                "If no human responds before the deadline the order auto-executes "
                "without review."
            )
        if facts["priority"] <= int(Priority.CRITICAL):
            risks.append("Marked CRITICAL priority — delay directly costs lives.")
        order = facts.get("order") or {}
        if order.get("site"):
            risks.append(f"Affected site: {order.get('site')}.")
        risks.append(
            "Acting on incomplete ground truth may misallocate scarce assets."
        )
        return risks

    def _deadline(self, facts: dict[str, Any]) -> str:
        if facts["human_only"]:
            return "No auto-execution deadline — awaiting human authorisation indefinitely."
        if facts["timeout_seconds"]:
            return (
                f"{facts['timeout_seconds']}s from escalation; "
                "auto-execution follows on timeout."
            )
        return "Decision window unspecified — treat as time-critical."

    def _build_prompt(self, facts: dict[str, Any]) -> str:
        """Compose the structured brief used both as the LLM prompt and the
        deterministic fallback body (PRD Step 7 / Step 10)."""
        risks = "\n".join(f"  - {r}" for r in self._risks(facts))
        reasoning = (
            "\n".join(f"  - {r}" for r in facts["reasoning"])
            if facts["reasoning"]
            else "  - (no upstream reasoning recorded)"
        )
        order = facts.get("order") or {}
        order_line = (
            f"team={order.get('team_id', '?')} site={order.get('site', '?')} "
            f"reason={order.get('reason', '')}".strip()
            if order
            else "(no order detail)"
        )
        return (
            "ESCALATION BRIEF FOR COMMANDER DASHBOARD\n"
            f"Report: {facts['report_id']}  |  Incident: {facts['incident_id']}  "
            f"|  Module: {facts['module']}  |  Priority: {facts['priority']}\n"
            "\n"
            "1. SITUATION SUMMARY\n"
            f"   {facts['summary']}\n"
            f"   Order: {order_line}\n"
            "\n"
            "2. WHY THIS EXCEEDED AUTONOMOUS AUTHORITY\n"
            f"   {self._why(facts)}\n"
            f"   Upstream reasoning:\n{reasoning}\n"
            "\n"
            "3. RECOMMENDED ACTION\n"
            f"   {facts['recommended_action']}\n"
            "\n"
            "4. KEY RISKS\n"
            f"{risks}\n"
            "\n"
            "5. DECISION DEADLINE\n"
            f"   {self._deadline(facts)}\n"
        )

    def _build_narrative(
        self,
        source: Message,
        facts: dict[str, Any],
        brief: str,
        prompt: str,
    ) -> Message:
        """Wrap the rendered brief in an ESCALATION_NARRATIVE message."""
        return Message(
            sender=self.name,
            recipient="human_dashboard",
            type=MessageType.ALERT,
            priority=source.priority,
            topic=ESCALATION_NARRATIVE,
            incident_id=source.incident_id,
            module=source.module if isinstance(source.module, Module) else Module.ALL,
            escalation_trigger=source.escalation_trigger,
            reasoning=[
                f"narrated escalation {facts['report_id']} via {self.client.name} client",
            ],
            payload={
                "kind": "escalation_narrative",
                "report_id": facts["report_id"],
                "source_message_id": source.id,
                "trigger": facts["trigger"],
                "human_only": facts["human_only"],
                "timeout_seconds": facts["timeout_seconds"],
                "client": self.client.name,
                "brief": brief,
                "sections": {
                    "situation_summary": facts["summary"],
                    "why_exceeded_authority": self._why(facts),
                    "recommended_action": facts["recommended_action"],
                    "key_risks": self._risks(facts),
                    "decision_deadline": self._deadline(facts),
                },
                "generated_at": utcnow_iso(),
            },
        )
