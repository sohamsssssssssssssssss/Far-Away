"""Wire model for mutual-aid federation.

Pure, serialisable dataclasses for the three nouns the federation layer trades:

  * :class:`District` — an adjacent peer in the registry (id, endpoint, state and
    the assets it currently has spare).
  * :class:`AidRequest` — "district X needs N of asset-type T for gap G at
    priority P" (PRD Step 4).
  * :class:`AidOffer` — a peer's answer: an offer sized to spare capacity, or a
    decline when it has nothing to give.

Each request/offer round-trips to and from a
:class:`~disastermind.core.contracts.Message` payload via the ``*_to_message`` /
``*_from_message`` helpers, so federation traffic rides the normal bus and lands
in the audit log like every other inter-agent message.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from ..core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Priority,
)
from ..models.domain import AssetType

# Topic federation messages travel on (kept local so we add nothing to the
# frozen Topic registry).
FEDERATION_TOPIC = "federation.mutual_aid"

# Payload kind markers so a consumer can tell a request from an offer.
_KIND_REQUEST = "mutual_aid_request"
_KIND_OFFER = "mutual_aid_offer"


class AidDecision(str, Enum):
    """A peer's response to an incoming mutual-aid request."""

    OFFER = "offer"
    DECLINE = "decline"


@dataclass
class District:
    """An adjacent district / peer in the federation registry.

    ``available`` maps :class:`AssetType` -> count currently spare. ``state`` is
    used to decide autonomy: aid that stays in-state is autonomous (Step 4),
    aid that crosses a state line escalates (Step 7).
    """

    district_id: str
    name: str
    state: str
    endpoint: str  # e.g. "https://district-b.example/aid" — never called in dry-run
    centroid_lat: float = 0.0
    centroid_lon: float = 0.0
    available: dict[AssetType, int] = field(default_factory=dict)

    def spare(self, asset_type: AssetType) -> int:
        """How many of ``asset_type`` this district has spare (>= 0)."""
        return max(0, int(self.available.get(asset_type, 0)))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["available"] = {k.value: int(v) for k, v in self.available.items()}
        return d


@dataclass
class AidRequest:
    """A request for mutual aid emitted toward a peer district (PRD Step 4)."""

    request_id: str
    from_district: str
    to_district: str
    zone_id: str  # the gap's zone
    asset_type: AssetType
    quantity: int
    priority: Priority
    cross_state: bool = False
    note: str = ""

    @staticmethod
    def new(
        from_district: str,
        to_district: str,
        zone_id: str,
        asset_type: AssetType,
        quantity: int,
        priority: Priority = Priority.HIGH,
        *,
        cross_state: bool = False,
        note: str = "",
        request_id: str | None = None,
    ) -> "AidRequest":
        return AidRequest(
            request_id=request_id or f"AID-REQ-{uuid.uuid4().hex[:12]}",
            from_district=from_district,
            to_district=to_district,
            zone_id=zone_id,
            asset_type=asset_type,
            quantity=int(quantity),
            priority=priority,
            cross_state=bool(cross_state),
            note=note,
        )

    @property
    def escalation_trigger(self) -> EscalationTrigger | None:
        """Cross-state aid needs human sign-off (Step 7); in-state is autonomous."""
        return EscalationTrigger.CROSS_STATE_RESOURCE if self.cross_state else None


@dataclass
class AidOffer:
    """A peer's answer to an :class:`AidRequest` (PRD Step 4)."""

    request_id: str
    from_district: str  # the responder
    to_district: str  # the original requester
    asset_type: AssetType
    decision: AidDecision
    quantity: int = 0  # offered count (0 when declined)
    note: str = ""

    @property
    def is_offer(self) -> bool:
        return self.decision is AidDecision.OFFER and self.quantity > 0


# --------------------------------------------------------------------------- bus glue
def request_to_message(
    req: AidRequest,
    *,
    sender: str = "federation.coordinator",
    incident_id: str | None = None,
) -> Message:
    """Wrap an :class:`AidRequest` in a :class:`Message` so it can ride the bus.

    A cross-state request carries
    :class:`EscalationTrigger.CROSS_STATE_RESOURCE` and travels as an
    ``ESCALATION`` so the commander routes it for human approval; an in-state
    request is a plain ``QUERY`` the peer can answer autonomously.
    """
    cross = req.cross_state
    payload: dict[str, Any] = {
        "kind": _KIND_REQUEST,
        "request_id": req.request_id,
        "from_district": req.from_district,
        "to_district": req.to_district,
        "zone_id": req.zone_id,
        "asset_type": req.asset_type.value,
        "quantity": int(req.quantity),
        "priority": int(req.priority),
        "cross_state": cross,
        "note": req.note,
    }
    return Message(
        sender=sender,
        recipient=req.to_district,
        type=MessageType.ESCALATION if cross else MessageType.QUERY,
        priority=req.priority,
        payload=payload,
        reasoning=[
            f"mutual-aid request: {req.quantity}x {req.asset_type.value} "
            f"for zone {req.zone_id}",
            "cross-state — requires human approval (PRD Step 7)"
            if cross
            else "in-state adjacent district — autonomous (PRD Step 4)",
        ],
        topic=FEDERATION_TOPIC,
        incident_id=incident_id,
        escalation_trigger=req.escalation_trigger,
    )


def request_from_message(msg: Message) -> AidRequest:
    """Reconstruct an :class:`AidRequest` from a bus :class:`Message` payload."""
    p = msg.payload
    if p.get("kind") != _KIND_REQUEST:
        raise ValueError(f"not a mutual-aid request payload: kind={p.get('kind')!r}")
    return AidRequest(
        request_id=p["request_id"],
        from_district=p["from_district"],
        to_district=p["to_district"],
        zone_id=p["zone_id"],
        asset_type=AssetType(p["asset_type"]),
        quantity=int(p["quantity"]),
        priority=Priority(int(p["priority"])),
        cross_state=bool(p.get("cross_state", False)),
        note=p.get("note", ""),
    )


def offer_to_message(
    offer: AidOffer,
    *,
    sender: str | None = None,
    incident_id: str | None = None,
    priority: Priority = Priority.HIGH,
) -> Message:
    """Wrap an :class:`AidOffer` in a :class:`Message` addressed to the requester."""
    payload: dict[str, Any] = {
        "kind": _KIND_OFFER,
        "request_id": offer.request_id,
        "from_district": offer.from_district,
        "to_district": offer.to_district,
        "asset_type": offer.asset_type.value,
        "decision": offer.decision.value,
        "quantity": int(offer.quantity),
        "note": offer.note,
    }
    verb = (
        f"offer {offer.quantity}x {offer.asset_type.value}"
        if offer.is_offer
        else f"decline {offer.asset_type.value} (no spare capacity)"
    )
    return Message(
        sender=sender or offer.from_district,
        recipient=offer.to_district,
        type=MessageType.ACK,
        priority=priority,
        payload=payload,
        reasoning=[f"mutual-aid response to {offer.request_id}: {verb}"],
        topic=FEDERATION_TOPIC,
        incident_id=incident_id,
    )


def offer_from_message(msg: Message) -> AidOffer:
    """Reconstruct an :class:`AidOffer` from a bus :class:`Message` payload."""
    p = msg.payload
    if p.get("kind") != _KIND_OFFER:
        raise ValueError(f"not a mutual-aid offer payload: kind={p.get('kind')!r}")
    return AidOffer(
        request_id=p["request_id"],
        from_district=p["from_district"],
        to_district=p["to_district"],
        asset_type=AssetType(p["asset_type"]),
        decision=AidDecision(p["decision"]),
        quantity=int(p.get("quantity", 0)),
        note=p.get("note", ""),
    )
