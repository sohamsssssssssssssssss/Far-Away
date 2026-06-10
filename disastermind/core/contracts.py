"""Inter-agent message contracts and shared enums.

Every message that crosses an agent boundary in DisasterMind is a :class:`Message`.
This module is the single source of truth for the wire format described in
PRD Group A, Step 9 (Decision Logging) and is imported by every tier.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any


class Tier(IntEnum):
    """Authority tier. Lower number == more authority."""

    COMMANDER = 1  # reviews & escalates
    SPECIALIST = 2  # prediction / optimisation / coordination — autonomous decisions
    EDGE = 3  # ingestion / dispatch — NO decision authority


class Module(StrEnum):
    """Disaster domain a message/agent belongs to."""

    CYCLONE_FLOOD = "A"
    EARTHQUAKE = "B"
    FIRE_COLLAPSE = "C"
    ALL = "ALL"


class Priority(IntEnum):
    """Message priority 1 (most urgent) .. 5 (informational). PRD Step 9."""

    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    INFO = 5


class MessageType(StrEnum):
    """PRD Step 9 message taxonomy."""

    ALERT = "alert"
    INSTRUCTION = "instruction"
    QUERY = "query"
    ACK = "acknowledgement"
    ESCALATION = "escalation"


class EscalationTrigger(StrEnum):
    """PRD Step 7 — decisions that require human approval."""

    CROSS_STATE_RESOURCE = "cross_state_resource_request"
    MILITARY_ASSET = "military_asset_deployment"
    MASS_EVACUATION = "mandatory_evacuation_gt_10000"
    REQUISITION_PRIVATE = "requisition_private_infrastructure"
    MEDIA_BROADCAST = "media_broadcast_order"
    # human-only (agent never auto-executes even on timeout)
    INTERNATIONAL_AID = "international_aid_request"
    STATE_OF_EMERGENCY = "declare_state_of_emergency"
    ARMED_FORCES_CIVIL = "armed_forces_in_civil_situation"
    CRITICAL_NATIONAL_INFRA = "critical_national_infrastructure"


# Triggers the Commander may auto-execute after timeout vs. those it may never act on alone.
HUMAN_ONLY_TRIGGERS: frozenset[EscalationTrigger] = frozenset(
    {
        EscalationTrigger.INTERNATIONAL_AID,
        EscalationTrigger.STATE_OF_EMERGENCY,
        EscalationTrigger.ARMED_FORCES_CIVIL,
        EscalationTrigger.CRITICAL_NATIONAL_INFRA,
    }
)


def utcnow_iso() -> str:
    """ISO 8601 timestamp in UTC (PRD Step 9 requirement)."""
    return datetime.now(UTC).isoformat()


@dataclass
class Message:
    """Canonical inter-agent envelope.

    Carries the full reasoning chain so the audit trail (Step 9) is complete
    without out-of-band lookups.
    """

    sender: str
    recipient: str
    type: MessageType
    priority: Priority
    payload: dict[str, Any] = field(default_factory=dict)
    reasoning: list[str] = field(default_factory=list)
    ttl_seconds: int = 300
    topic: str = "default"
    incident_id: str | None = None
    module: Module = Module.ALL
    escalation_trigger: EscalationTrigger | None = None
    timestamp: str = field(default_factory=utcnow_iso)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["priority"] = int(self.priority)
        d["module"] = self.module.value
        d["escalation_trigger"] = (
            self.escalation_trigger.value if self.escalation_trigger else None
        )
        return d

    def reply(
        self,
        sender: str,
        type: MessageType = MessageType.ACK,
        payload: dict[str, Any] | None = None,
        reasoning: list[str] | None = None,
    ) -> Message:
        """Build a correlated reply addressed back to this message's sender."""
        return Message(
            sender=sender,
            recipient=self.sender,
            type=type,
            priority=self.priority,
            payload=payload or {},
            reasoning=reasoning or [],
            topic=self.topic,
            incident_id=self.incident_id,
            module=self.module,
        )


# Well-known topic names so producers/consumers agree without hard-coding strings.
class Topic:
    RAW_FEED = "tier3.raw_feed"
    IOT_TELEMETRY = "tier3.iot_telemetry"
    PREDICTION = "tier2.prediction"
    CASCADE = "tier2.cascade"
    RESOURCE_PLAN = "tier2.resource_plan"
    ROUTING_PLAN = "tier2.routing_plan"
    FIELD_ORDER = "tier2.field_order"
    COMMANDER_REVIEW = "tier1.commander_review"
    ESCALATION = "tier1.escalation"
    DISPATCH = "tier3.dispatch"
