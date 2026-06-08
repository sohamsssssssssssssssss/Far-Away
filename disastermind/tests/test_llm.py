"""Tests for the Group B LLM escalation layer (PRD Step 7).

Stdlib-only, no network. Verifies:
  * :class:`TemplateClient` renders a deterministic, structured brief offline.
  * :func:`make_client` falls back to the template client without an API key
    and selects the Anthropic client when a key is present.
  * :class:`EscalationNarrator` reacts to a synthetic ``Topic.ESCALATION``
    message and emits an ``ESCALATION_NARRATIVE`` message.
  * The narrator is advisory only (no decision authority) and ignores
    non-escalation traffic on the topic.
"""
from __future__ import annotations

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from disastermind.llm import (
    ESCALATION_NARRATIVE,
    AnthropicClient,
    EscalationNarrator,
    TemplateClient,
    make_client,
)
from disastermind.llm import build as llm_build


def _escalation_msg(human_only: bool = False) -> Message:
    """A synthetic Commander ESCALATION message (mirrors commander/agent.py)."""
    return Message(
        sender="commander",
        recipient="human_dashboard",
        type=MessageType.ESCALATION,
        priority=Priority.CRITICAL,
        topic=Topic.ESCALATION,
        incident_id="usgs:eq-escalate",
        module=Module.EARTHQUAKE,
        escalation_trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
        reasoning=["matrix: cross-state resource exceeds autonomy"],
        payload={
            "kind": "escalation",
            "report_id": "esc-abc123",
            "human_only": human_only,
            "timeout_seconds": 300,
            "report": {
                "report_id": "esc-abc123",
                "trigger": EscalationTrigger.CROSS_STATE_RESOURCE.value,
                "summary": "needs a neighbouring state's NDRF battalion",
                "recommended_action": "Auto-execute in 300s unless a human responds.",
                "timeout_seconds": 300,
                "human_only": human_only,
                "supporting": {
                    "order": {
                        "team_id": "NDRF-99",
                        "site": "cross-border-zone",
                        "reason": "cross-state mutual aid required",
                    },
                    "incident_id": "usgs:eq-escalate",
                    "deadline_epoch": 1_000_300.0,
                },
            },
        },
    )


# ----------------------------------------------------------------- client tests
def test_template_client_is_deterministic_and_offline():
    client = TemplateClient()
    prompt = "ESCALATION BRIEF\n1. SITUATION\n   foo\n"
    assert client.generate(prompt) == prompt
    # Same input -> same output, with no network.
    assert client.generate(prompt) == client.generate(prompt)
    assert client.name == "template"


def test_make_client_falls_back_to_template_without_key(monkeypatch):
    for var in ("DM_ANTHROPIC_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    client = make_client(Settings())
    assert isinstance(client, TemplateClient)


def test_make_client_selects_anthropic_with_key(monkeypatch):
    monkeypatch.delenv("DM_ANTHROPIC_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    client = make_client(Settings())
    assert isinstance(client, AnthropicClient)
    assert client.model == "claude-opus-4-8"


def test_anthropic_client_degrades_to_prompt_without_sdk(monkeypatch):
    # No SDK installed in the test env -> generate() must not raise / hit network.
    monkeypatch.delenv("DM_ANTHROPIC_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    client = AnthropicClient(api_key="sk-test-key")
    prompt = "hello brief"
    assert client.generate(prompt) == prompt


# --------------------------------------------------------------- narrator tests
def test_narrator_emits_narrative_on_escalation(monkeypatch):
    for var in ("DM_ANTHROPIC_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    bus = InMemoryBus()
    logger = DecisionLogger.null()
    narrator = EscalationNarrator(bus=bus, logger=logger, settings=Settings())

    assert narrator.tier is Tier.COMMANDER
    assert narrator.decision_authority is False
    assert isinstance(narrator.client, TemplateClient)

    bus.publish(_escalation_msg())

    narratives = [m for m in bus.history if m.topic == ESCALATION_NARRATIVE]
    assert narratives, "narrator did not emit an ESCALATION_NARRATIVE message"
    out = narratives[-1]
    assert out.type is MessageType.ALERT
    assert out.payload["kind"] == "escalation_narrative"
    assert out.payload["report_id"] == "esc-abc123"
    assert out.payload["client"] == "template"
    assert out.escalation_trigger is EscalationTrigger.CROSS_STATE_RESOURCE
    assert out.incident_id == "usgs:eq-escalate"

    # All five required brief sections are present and populated.
    sections = out.payload["sections"]
    for key in (
        "situation_summary",
        "why_exceeded_authority",
        "recommended_action",
        "key_risks",
        "decision_deadline",
    ):
        assert sections.get(key), f"missing/empty brief section: {key}"

    brief = out.payload["brief"]
    assert "SITUATION SUMMARY" in brief
    assert "WHY THIS EXCEEDED AUTONOMOUS AUTHORITY" in brief
    assert "RECOMMENDED ACTION" in brief
    assert "KEY RISKS" in brief
    assert "DECISION DEADLINE" in brief
    assert EscalationTrigger.CROSS_STATE_RESOURCE.value in brief


def test_narrator_human_only_deadline_text(monkeypatch):
    for var in ("DM_ANTHROPIC_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    bus = InMemoryBus()
    narrator = EscalationNarrator(bus=bus, logger=DecisionLogger.null())
    out = narrator.handle(_escalation_msg(human_only=True))
    assert len(out) == 1
    deadline = out[0].payload["sections"]["decision_deadline"]
    assert "No auto-execution" in deadline
    assert out[0].payload["human_only"] is True


def test_narrator_ignores_non_escalation_traffic():
    bus = InMemoryBus()
    narrator = EscalationNarrator(bus=bus, logger=DecisionLogger.null())
    # A rejection ACK published on Topic.ESCALATION must NOT be narrated.
    ack = Message(
        sender="commander",
        recipient="human_dashboard",
        type=MessageType.ACK,
        priority=Priority.HIGH,
        topic=Topic.ESCALATION,
        payload={"kind": "escalation_rejected", "report_id": "esc-abc123"},
    )
    assert narrator.handle(ack) == []
    bus.publish(ack)
    assert not [m for m in bus.history if m.topic == ESCALATION_NARRATIVE]


def test_build_agents_returns_narrator():
    bus = InMemoryBus()
    agents = llm_build.build_agents(bus, DecisionLogger.null(), Settings())
    assert len(agents) == 1
    assert isinstance(agents[0], EscalationNarrator)
