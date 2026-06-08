"""LLM client abstraction for the escalation layer (PRD Step 7).

The narrator depends only on the tiny :class:`LLMClient` protocol
(``generate(prompt: str) -> str``). Two implementations are provided:

  * :class:`AnthropicClient` — lazily imports the ``anthropic`` SDK and calls the
    ``claude-opus-4-8`` model. Used ONLY when an API key is configured
    (``DM_ANTHROPIC_KEY`` or ``ANTHROPIC_API_KEY``).
  * :class:`TemplateClient` — a deterministic, network-free renderer that turns
    the escalation prompt into a clear structured brief using the standard
    library alone. This is the default fallback (PRD Step 10).

:func:`make_client` selects the right implementation from
:class:`~disastermind.core.config.Settings` + the environment. If the SDK or key
is missing, or the SDK call raises, we always fall back to :class:`TemplateClient`
so no test path ever touches the network.
"""
from __future__ import annotations

import abc
import os

from ..core.config import Settings

#: The single model this layer is authorised to call (spec requirement).
ANTHROPIC_MODEL = "claude-opus-4-8"

#: Environment variables that may carry the Anthropic API key.
KEY_ENV_VARS = ("DM_ANTHROPIC_KEY", "ANTHROPIC_API_KEY")


def _resolve_api_key(settings: Settings | None = None) -> str:
    """Return the first non-empty Anthropic key from settings/env, else ""."""
    key = getattr(settings, "anthropic_key", "") if settings is not None else ""
    if key:
        return key
    for var in KEY_ENV_VARS:
        val = os.environ.get(var, "")
        if val:
            return val
    return ""


class LLMClient(abc.ABC):
    """Minimal text-completion contract the narrator codes against."""

    name: str = "llm"

    @abc.abstractmethod
    def generate(self, prompt: str) -> str:
        """Return a completion for ``prompt`` (single string in, single out)."""


class TemplateClient(LLMClient):
    """Deterministic, offline brief renderer (PRD Step 10 fallback).

    Echoes the structured prompt straight back. The narrator builds a
    fully-formed, human-readable brief as the prompt body, so a no-op "model"
    that returns the prompt verbatim yields a clear, reproducible brief with no
    network dependency. This keeps every test path deterministic.
    """

    name = "template"

    def generate(self, prompt: str) -> str:
        return prompt


class AnthropicClient(LLMClient):
    """Real Claude client — lazily imported, key-gated (PRD Step 7).

    The ``anthropic`` SDK is imported inside :meth:`generate` so the package
    imports cleanly without the optional dependency. Any failure (missing SDK,
    transport error, unexpected response shape) degrades to the prompt text so
    the caller — :class:`~disastermind.llm.narrator.EscalationNarrator` — still
    produces a usable brief.
    """

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = ANTHROPIC_MODEL,
        max_tokens: int = 1024,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

    def generate(self, prompt: str) -> str:
        try:
            import anthropic  # type: ignore  # lazy: optional dependency

            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._extract_text(resp) or prompt
        except Exception:  # missing SDK / network / shape — degrade gracefully
            return prompt

    @staticmethod
    def _extract_text(resp: object) -> str:
        """Pull plain text from an Anthropic Messages response defensively."""
        content = getattr(resp, "content", None)
        if not content:
            return ""
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()


def make_client(settings: Settings | None = None) -> LLMClient:
    """Pick the right :class:`LLMClient` (PRD Step 7).

    Returns an :class:`AnthropicClient` only when an API key is present; otherwise
    the deterministic, network-free :class:`TemplateClient`.
    """
    key = _resolve_api_key(settings)
    if key:
        return AnthropicClient(api_key=key)
    return TemplateClient()
