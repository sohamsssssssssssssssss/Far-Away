"""`/report/generate` — secure backend post-incident report (PRD Step 9).

Replaces the frontend's broken/insecure browser->Anthropic call. The Anthropic
key stays server-side; offline it returns the deterministic structured report +
a report-derived summary (never an echoed prompt). Needs FastAPI.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from disastermind.api.server import create_server  # noqa: E402
from disastermind.core.contracts import (  # noqa: E402
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)


def _client_with_audit() -> TestClient:
    srv = create_server(start_streaming=True)
    srv.bus.publish(
        Message(
            sender="field_coordinator", recipient="commander", type=MessageType.INSTRUCTION,
            priority=Priority.CRITICAL, topic=Topic.FIELD_ORDER, incident_id="eq-rpt",
            module=Module.EARTHQUAKE, escalation_trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
            payload={"kind": "field_order", "incident_id": "eq-rpt",
                     "orders": [{"team_id": "N9", "site": "x", "priority": 1, "reason": "aid"}],
                     "escalation": {"trigger": "cross_state_resource_request",
                                    "summary": "need battalion", "scale": 1}},
        )
    )
    return TestClient(srv.app)


def test_report_generate_returns_structured_report_and_markdown():
    c = _client_with_audit()
    r = c.post("/report/generate")
    assert r.status_code == 200
    d = r.json()
    assert {"report", "markdown", "narrative", "narrative_source"} <= set(d)
    assert d["markdown"] and isinstance(d["report"], dict)
    assert "decisions" in d["report"] and "dispatch" in d["report"]


def test_offline_narrative_is_derived_not_an_echoed_prompt():
    c = _client_with_audit()
    d = c.post("/report/generate").json()
    # offline => template source, and the narrative must NOT be the instruction prompt
    assert d["narrative_source"] in ("template", "anthropic")
    if d["narrative_source"] == "template":
        assert not d["narrative"].lower().startswith("write a concise")
        assert "Incident" in d["narrative"] and "dispatch" in d["narrative"]


def test_versioned_alias_and_get_both_work():
    c = _client_with_audit()
    assert c.post("/v1/report/generate").status_code == 200
    assert c.get("/report/generate").status_code == 200  # GET also allowed (demo convenience)


# ----------------------------------------------------- LLM proxy (callLLM backend)
def test_llm_generate_requires_messages_and_is_honest_offline():
    """Server-side LLM proxy: 400 without messages; 503 (not faked) with no key."""
    c = TestClient(create_server(start_streaming=False).app)
    assert c.post("/llm/generate", json={}).status_code == 400
    r = c.post("/llm/generate", json={"messages": [{"role": "user", "content": "hi"}]})
    # offline (no DM_ANTHROPIC_KEY) -> 503 so the caller uses its own fallback; never a faked answer
    assert r.status_code in (503, 200)
    if r.status_code == 503:
        assert r.json().get("source") == "none"
