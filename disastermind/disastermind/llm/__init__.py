"""Group B LLM escalation layer (PRD Step 7).

Turns a Tier-1 ``Topic.ESCALATION`` event into a rich, human-readable escalation
brief for the commander dashboard. The package is stdlib-only by default: the
``anthropic`` SDK is imported lazily and used ONLY when an API key is present;
otherwise a deterministic :class:`TemplateClient` renders the brief offline with
no network access (PRD Step 10 graceful degradation).
"""
from __future__ import annotations

from .client import (
    AnthropicClient,
    LLMClient,
    TemplateClient,
    make_client,
)
from .advisor import (
    DecisionSupportAdvisor,
    PublicAlert,
    ReallocationAdvice,
    ReallocationMove,
)
from .narrator import ESCALATION_NARRATIVE, EscalationNarrator

__all__ = [
    "LLMClient",
    "TemplateClient",
    "AnthropicClient",
    "make_client",
    "EscalationNarrator",
    "ESCALATION_NARRATIVE",
    "DecisionSupportAdvisor",
    "PublicAlert",
    "ReallocationAdvice",
    "ReallocationMove",
]
