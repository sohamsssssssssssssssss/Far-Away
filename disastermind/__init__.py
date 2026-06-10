"""DisasterMind — autonomous multi-agent disaster coordination (PRD Group A).

Tiers:
  * Tier 3 (edge): ingestion, IoT gateway, dispatch — no decision authority.
  * Tier 2 (specialist): prediction, cascade, resource, routing, field coord.
  * Tier 1 (commander): authority review + escalation.

Public re-exports for convenience.
"""
from .core.contracts import (  # noqa: F401
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)

__version__ = "0.1.0"
