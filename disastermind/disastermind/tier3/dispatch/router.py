"""DispatchRouter — Tier 3 notification-execution agent (PRD Step 8).

The router subscribes to :data:`~disastermind.core.contracts.Topic.DISPATCH` and
routes each order to the right delivery channel by ``payload["channel"]``. It has
**no decision authority** (``decision_authority = False``): it never originates or
alters an order, it only EXECUTES the autonomous-dispatch (or human-approved)
orders the Commander (Tier 1) publishes.

Routing:
  * ``payload["channel"]`` selects one channel by its ``name`` key.
  * ``channel == "all"`` (or a list) fans out to multiple channels — used for
    mass public warnings (SMS + CAP + push simultaneously).
  * Unknown channels fall back to the SMS channel (lowest common denominator)
    and the substitution is recorded in the receipt + reasoning.

Each delivery yields a receipt; the router publishes a single ``ACK`` Message
back to the Commander summarising the receipts so the audit trail (Step 9) is
complete. Channel failures degrade gracefully and never raise (Step 10).
"""
from __future__ import annotations

import logging
from typing import Any

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.config import Settings
from ...core.contracts import Message, MessageType, Priority, Tier, Topic
from .channels import Channel

log = logging.getLogger("disastermind.dispatch.router")


class DispatchRouter(BaseAgent):
    """Routes ``Topic.DISPATCH`` orders to notification channels (PRD Step 8)."""

    tier = Tier.EDGE
    decision_authority = False  # Tier 3 executes only — never decides.

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        channels: list[Channel] | None = None,
        settings: Settings | None = None,
        name: str = "dispatch.router",
    ) -> None:
        super().__init__(name=name, bus=bus, logger=logger, subscriptions=[Topic.DISPATCH])
        self.settings = settings or Settings()
        self.channels: dict[str, Channel] = {c.name: c for c in (channels or [])}
        #: lowest-common-denominator fallback when a channel is unknown.
        self._fallback_name = "sms" if "sms" in self.channels else next(
            iter(self.channels), None
        )

    # ------------------------------------------------------------------ routing
    def _select(self, channel: Any) -> list[Channel]:
        """Resolve ``payload["channel"]`` to one or more concrete channels."""
        if channel in (None, "all", "*", "broadcast_all"):
            return list(self.channels.values())
        if isinstance(channel, (list, tuple, set)):
            chosen: list[Channel] = []
            for key in channel:
                chosen.extend(self._select(key))
            # de-duplicate while preserving order
            seen: set[str] = set()
            uniq: list[Channel] = []
            for c in chosen:
                if c.name not in seen:
                    seen.add(c.name)
                    uniq.append(c)
            return uniq
        key = str(channel).lower()
        if key in self.channels:
            return [self.channels[key]]
        # alias a couple of common synonyms before falling back
        aliases = {"fcm": "push", "notification": "push", "sat": "iridium",
                   "satellite": "iridium", "text": "sms", "broadcast": "cap"}
        if aliases.get(key) in self.channels:
            return [self.channels[aliases[key]]]
        return []

    def handle(self, message: Message) -> list[Message]:
        """Execute one DISPATCH order; ACK the Commander with the receipts."""
        if message.topic != Topic.DISPATCH:
            return []
        payload = message.payload or {}
        # The router both consumes and publishes its ACK on Topic.DISPATCH, so it
        # must ignore its own housekeeping ACKs — otherwise an ACK (which carries
        # no "channel") would fan out to every channel and recurse forever.
        if message.sender == self.name or payload.get("kind") == "dispatch_ack":
            return []
        if message.type is MessageType.ACK:
            return []
        requested = payload.get("channel")
        targets = self._select(requested)
        reasoning: list[str] = []

        if not targets:
            if self._fallback_name and self._fallback_name in self.channels:
                targets = [self.channels[self._fallback_name]]
                reasoning.append(
                    f"unknown channel {requested!r}; fell back to {self._fallback_name!r}"
                )
            else:
                reasoning.append(f"no channel for {requested!r} and no fallback available")
                return [self._ack(message, [], reasoning)]

        receipts: list[dict[str, Any]] = []
        for ch in targets:
            try:
                receipt = ch.send(payload)
            except Exception as exc:  # defence in depth — channel.send already guards
                log.exception("channel %s send raised", ch.name)
                receipt = {
                    "kind": "dispatch_receipt", "channel": ch.name,
                    "status": "failed", "detail": f"{type(exc).__name__}: {exc}",
                    "recipients": payload.get("recipients", []),
                }
            receipts.append(receipt)
            reasoning.append(
                f"{ch.name}: {receipt.get('status')} "
                f"({receipt.get('detail', '')})".strip()
            )

        return [self._ack(message, receipts, reasoning)]

    # --------------------------------------------------------------------- ack
    def _ack(
        self, message: Message, receipts: list[dict[str, Any]], reasoning: list[str]
    ) -> Message:
        """Build the audit ACK summarising delivery back to the Commander."""
        delivered = sum(1 for r in receipts if r.get("status") in ("sent", "recorded"))
        failed = sum(1 for r in receipts if r.get("status") == "failed")
        ack = message.reply(
            sender=self.name,
            type=MessageType.ACK,
            payload={
                "kind": "dispatch_ack",
                "incident_id": message.incident_id,
                "requested_channel": (message.payload or {}).get("channel"),
                "delivered": delivered,
                "failed": failed,
                "receipts": receipts,
            },
            reasoning=reasoning,
        )
        # ACKs are housekeeping; never higher priority than the order itself.
        ack.priority = Priority.LOW
        ack.topic = Topic.DISPATCH
        return ack
