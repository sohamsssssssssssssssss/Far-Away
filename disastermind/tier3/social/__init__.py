"""Tier 3 social-media NLP package (PRD Step 1 Module C trigger / Step 2).

Exposes the :class:`~disastermind.tier3.social.agent.SocialNLPAgent` and the
uniform :func:`~disastermind.tier3.social.build.build_agents` factory used by the
orchestration layer.
"""
from __future__ import annotations

from .agent import (
    SOCIAL_RAW_FEED_RECIPIENT,
    SocialNLPAgent,
    SocialPost,
    score_post,
)

__all__ = [
    "SocialNLPAgent",
    "SocialPost",
    "score_post",
    "SOCIAL_RAW_FEED_RECIPIENT",
]
