"""End-to-end: a synthetic earthquake through the full DAG (PRD Group A Step 3).

Drives a synthetic USGS M4.9 (the offline sample) through the entire wired agent
DAG on an in-memory bus and asserts the load-bearing chain produces a
``Topic.DISPATCH`` (and, with an escalation trigger, a ``Topic.ESCALATION``):

    RAW_FEED -> PREDICTION -> CASCADE -> RESOURCE_PLAN -> ROUTING_PLAN
             -> FIELD_ORDER -> DISPATCH / ESCALATION

Also asserts the tamper-evident audit hash-chain
(:meth:`DecisionLogger.verify_chain`) verifies True for an untampered log and
False once a record is altered.
"""
from __future__ import annotations

import json

from disastermind.core.contracts import MessageType, Topic


def test_synthetic_earthquake_reaches_dispatch(harness):
    """A synthetic earthquake must flow all the way to a real DISPATCH order."""
    harness.seed_field_teams()
    harness.run_ingestion_tick()

    counts = harness.topic_counts()
    # Every stage of the load-bearing chain must fire.
    assert counts.get(Topic.RAW_FEED, 0) > 0, "no RAW_FEED emitted"
    assert counts.get(Topic.PREDICTION, 0) > 0, "no PREDICTION emitted"
    assert counts.get(Topic.CASCADE, 0) > 0, "no CASCADE emitted"
    assert counts.get(Topic.RESOURCE_PLAN, 0) > 0, "no RESOURCE_PLAN emitted"
    assert counts.get(Topic.ROUTING_PLAN, 0) > 0, "no ROUTING_PLAN emitted (dead edge)"
    assert counts.get(Topic.FIELD_ORDER, 0) > 0, "no FIELD_ORDER emitted"

    dispatches = harness.real_dispatches()
    assert dispatches, "pipeline produced 0 DISPATCH (load-bearing break)"


def test_earthquake_path_produces_cascade_for_module_b(harness):
    """The earthquake DAG must exercise the prediction->cascade edge (Module B)."""
    harness.seed_field_teams()
    harness.run_ingestion_tick()

    cascade_msgs = harness.messages_on(Topic.CASCADE)
    assert cascade_msgs
    from disastermind.core.contracts import Module

    assert any(m.module is Module.EARTHQUAKE for m in cascade_msgs), (
        "no earthquake (Module B) cascade produced"
    )


def test_dispatch_messages_are_routed_to_channels(harness):
    """Each real DISPATCH order should be acknowledged by the dispatch router."""
    harness.seed_field_teams()
    harness.run_ingestion_tick()

    acks = [
        m
        for m in harness.messages_on(Topic.DISPATCH)
        if (m.payload or {}).get("kind") == "dispatch_ack"
    ]
    assert acks, "dispatch router produced no delivery ACKs"
    # At least one delivery should have been recorded (dry-run channels record).
    assert any(int(a.payload.get("delivered", 0)) >= 1 for a in acks)


def test_escalation_path_on_cross_state_resource(harness):
    """A cross-state resource order escalates rather than auto-dispatching.

    We inject a FIELD_ORDER that carries an explicit cross-state escalation
    trigger; the Commander must publish a Topic.ESCALATION (PRD Step 7) instead
    of dispatching it autonomously.
    """
    from disastermind.core.contracts import (
        EscalationTrigger,
        Message,
        Module,
        Priority,
    )

    field_order = Message(
        sender="field_coordinator",
        recipient="commander",
        type=MessageType.INSTRUCTION,
        priority=Priority.CRITICAL,
        topic=Topic.FIELD_ORDER,
        incident_id="usgs:eq-escalate",
        module=Module.EARTHQUAKE,
        escalation_trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
        payload={
            "kind": "field_order",
            "incident_id": "usgs:eq-escalate",
            "orders": [
                {
                    "team_id": "NDRF-99",
                    "site": "cross-border-zone",
                    "priority": 1,
                    "reason": "cross-state mutual aid required",
                }
            ],
            "escalation": {
                "trigger": EscalationTrigger.CROSS_STATE_RESOURCE.value,
                "summary": "needs a neighbouring state's NDRF battalion",
                "scale": 1,
            },
        },
    )
    harness.bus.publish(field_order)

    escalations = harness.messages_on(Topic.ESCALATION)
    assert escalations, "cross-state order did not produce an ESCALATION"
    esc = escalations[-1]
    assert esc.type is MessageType.ESCALATION
    assert esc.escalation_trigger is EscalationTrigger.CROSS_STATE_RESOURCE


def test_audit_chain_verifies_then_breaks_on_tamper(disk_harness):
    """DecisionLogger.verify_chain(): True untampered, False after a record edit."""
    disk_harness.seed_field_teams()
    disk_harness.run_ingestion_tick()

    logger = disk_harness.logger
    # Something must have been logged through the pipeline.
    assert disk_harness.real_dispatches(), "no dispatch -> nothing meaningful logged"
    assert logger.verify_chain() is True, "untampered audit chain failed to verify"

    # Tamper: rewrite the body of a middle record on disk, leaving its hash.
    with open(logger.path, "r", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip()]
    assert len(lines) >= 2

    idx = len(lines) // 2
    rec = json.loads(lines[idx])
    # Mutate a payload field without recomputing the hash -> chain must break.
    rec["reasoning"] = list(rec.get("reasoning", [])) + ["TAMPERED"]
    lines[idx] = json.dumps(rec, separators=(",", ":")) + "\n"
    with open(logger.path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)

    assert logger.verify_chain() is False, "tampered audit chain still verified True"
