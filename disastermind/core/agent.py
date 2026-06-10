"""Base agent abstraction shared by all three tiers.

Design goals (PRD Group A):
  * Tier 3 agents observe/report only — ``decision_authority = False``.
  * Tier 2 agents make autonomous decisions within their domain.
  * Tier 1 (Commander) reviews Tier 2 output against the authority matrix.

Every agent:
  * subscribes to a set of topics on construction,
  * implements ``handle(message) -> list[Message]`` for reactive work,
  * may implement ``tick() -> list[Message]`` for the periodic 30 s loop,
  * emits via ``self.emit`` which records to the audit log AND publishes.
"""
from __future__ import annotations

import abc
import logging

from ..audit.decision_log import DecisionLogger
from .bus import MessageBus
from .contracts import Message, Tier

log = logging.getLogger("disastermind.agent")


class BaseAgent(abc.ABC):
    #: subclasses set these
    tier: Tier = Tier.SPECIALIST
    #: Tier 3 has no decision authority (PRD Step 2 / Step 8)
    decision_authority: bool = True

    def __init__(
        self,
        name: str,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        subscriptions: list[str] | None = None,
    ) -> None:
        self.name = name
        self.bus = bus
        self.logger = logger or DecisionLogger.null()
        self.subscriptions = subscriptions or []
        for topic in self.subscriptions:
            self.bus.subscribe(topic, self.name, self._on_message)
        log.info("agent %s (tier %s) online; subs=%s", name, int(self.tier), self.subscriptions)

    # ------------------------------------------------------------------ hooks
    @abc.abstractmethod
    def handle(self, message: Message) -> list[Message]:
        """React to one inbound message; return zero or more outbound messages."""

    def tick(self) -> list[Message]:
        """Periodic work invoked once per coordination cycle (PRD Step 10).

        Default: no periodic behaviour. Ingestion/prediction agents override.
        """
        return []

    # --------------------------------------------------------------- plumbing
    def _on_message(self, message: Message) -> None:
        try:
            for out in self.handle(message) or []:
                self.emit(out)
        except Exception:
            log.exception("agent %s failed handling message %s", self.name, message.id)

    def emit(self, message: Message) -> None:
        """Audit-log then publish. The single egress point for every agent."""
        if message.sender == "":
            message.sender = self.name
        self.logger.record(message)
        self.bus.publish(message)

    def run_tick(self) -> None:
        for out in self.tick() or []:
            self.emit(out)
