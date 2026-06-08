"""Tests for the post-incident reporting package (PRD Step 9 review).

We drive a real scenario from :mod:`disastermind.scenarios` (the same wired
agent DAG the e2e tests exercise), then assert that
:class:`disastermind.reporting.IncidentReporter` faithfully reconstructs the
after-action picture from the bus history + decision-logger trail: dispatch and
escalation counts, resource utilisation, SHAP explainability, and a non-empty
Markdown render that names the incident.

Standard-library only, offline, deterministic (PRD HARD RULE 2 / 4).
"""
from __future__ import annotations

import json

from disastermind import scenarios
from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    EscalationTrigger,
    Module,
    Topic,
)
from disastermind.reporting import (
    EscalationOutcome,
    IncidentReport,
    IncidentReporter,
)


# --------------------------------------------------------------------------- core
def test_report_captures_dispatch_from_driven_scenario() -> None:
    """A driven scenario's DISPATCH orders show up in the dispatch summary."""
    result = scenarios.run_scenario("B")
    loop = result.loop

    rep = IncidentReporter(bus=loop.bus, logger=loop.logger).generate()
    assert isinstance(rep, IncidentReport)

    # The reporter's order count must equal the scenario's real DISPATCH orders.
    assert rep.dispatch.total_orders == len(result.dispatches) > 0
    # Every counted order is attributed to an authorisation path + a channel.
    assert sum(rep.dispatch.by_via.values()) == rep.dispatch.total_orders
    assert sum(rep.dispatch.by_channel.values()) >= rep.dispatch.total_orders
    # Autonomous default scenario -> no human escalation pathway used.
    assert "autonomous" in rep.dispatch.by_via


def test_report_decision_breakdown_matches_history() -> None:
    """Decision counts add up to the number of analysed messages."""
    loop = scenarios.run_scenario("A").loop
    rep = IncidentReporter(bus=loop.bus, logger=loop.logger).generate()

    assert rep.decisions.total == rep.message_count == len(rep.timeline)
    assert sum(rep.decisions.by_tier.values()) == rep.decisions.total
    assert sum(rep.decisions.by_type.values()) == rep.decisions.total
    assert sum(rep.decisions.by_priority.values()) == rep.decisions.total
    # The Commander (Tier 1) produced the autonomous DISPATCH orders.
    assert rep.decisions.by_tier.get("COMMANDER", 0) >= rep.dispatch.total_orders


def test_timeline_is_iso_ordered() -> None:
    """The timeline is sorted ascending by ISO-8601 timestamp (PRD Step 9)."""
    loop = scenarios.run_scenario("C").loop
    rep = IncidentReporter(bus=loop.bus, logger=loop.logger).generate()
    stamps = [t.timestamp for t in rep.timeline]
    assert stamps == sorted(stamps)
    assert rep.window["start"] == stamps[0]
    assert rep.window["end"] == stamps[-1]


def test_resource_utilisation_reports_tasked_teams() -> None:
    """Resource section reports the field teams tasked across the response."""
    loop = scenarios.run_scenario("B").loop
    rep = IncidentReporter(bus=loop.bus, logger=loop.logger).generate()
    assert rep.resources.plans_issued > 0
    assert rep.resources.teams_tasked > 0
    assert rep.resources.teams_tasked == len(rep.resources.tasked_team_ids)
    # Asset types are inferred from the team-id prefixes in the roster.
    assert rep.resources.by_asset_type
    assert rep.resources.population_at_risk > 0


def test_explainability_rolls_up_shap_attributions() -> None:
    """Logged SHAP attributions are summarised per model (PRD Step 9)."""
    loop = scenarios.run_scenario("B").loop
    rep = IncidentReporter(bus=loop.bus, logger=loop.logger).generate()
    x = rep.explainability
    assert x.predictions_logged > 0
    assert x.models
    # Each model with attributions has a non-empty mean table and a top feature.
    for model, feats in x.mean_attributions.items():
        assert feats
        assert model in x.top_feature
        assert x.top_feature[model] in feats


# ----------------------------------------------------------------- escalations
def test_report_captures_escalation_pending() -> None:
    """The escalation variant surfaces a pending human-in-the-loop escalation."""
    result = scenarios.run_scenario("B", escalate=True)
    assert result.reached_escalation
    rep = IncidentReporter(bus=result.loop.bus, logger=result.loop.logger).generate()

    assert len(rep.escalations) == len(result.escalations) >= 1
    esc = rep.escalations[-1]
    assert isinstance(esc, EscalationOutcome)
    assert esc.trigger == EscalationTrigger.CROSS_STATE_RESOURCE.value
    # No human acted and the loop clock never advanced past the deadline.
    assert esc.outcome == "pending"


def _commander_with_escalation():
    """Build a lone Commander holding one open cross-state escalation."""
    from disastermind.tier1.commander.agent import CommanderAgent
    from disastermind.scenarios.base import inject_escalation_order

    bus = InMemoryBus()
    logger = DecisionLogger.null()
    cmd = CommanderAgent(bus, logger, Settings())
    inject_escalation_order(
        bus,
        module=Module.EARTHQUAKE,
        incident_id="inc-esc",
        trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
        team_id="NDRF-99",
        site="ward 12",
        reason="cross-state mutual aid",
        summary="needs neighbouring NDRF battalion",
    )
    report_id = next(iter(cmd.pending))
    return bus, logger, cmd, report_id


def test_escalation_outcome_approved() -> None:
    bus, logger, cmd, rid = _commander_with_escalation()
    cmd.approve(rid, approver="operator-jane")
    rep = IncidentReporter(bus=bus, logger=logger).generate()
    assert rep.escalations[0].outcome == "approved"
    assert "operator-jane" in rep.escalations[0].detail


def test_escalation_outcome_rejected() -> None:
    bus, logger, cmd, rid = _commander_with_escalation()
    cmd.reject(rid, approver="operator-jane", note="not justified")
    rep = IncidentReporter(bus=bus, logger=logger).generate()
    assert rep.escalations[0].outcome == "rejected"


def test_escalation_outcome_auto_executed_on_timeout() -> None:
    bus, logger, cmd, rid = _commander_with_escalation()
    cmd.resolve_pending(now_epoch=10**12)  # far past any deadline
    rep = IncidentReporter(bus=bus, logger=logger).generate()
    assert rep.escalations[0].outcome == "auto_executed"


# --------------------------------------------------------------------- rendering
def test_markdown_is_non_empty_and_names_incident() -> None:
    """Markdown render is non-empty and contains the incident id (PRD Step 9)."""
    incident_id = scenarios.earthquake.INCIDENT_ID
    loop = scenarios.simulate_earthquake()
    rep = IncidentReporter(bus=loop.bus, logger=loop.logger)

    md = rep.to_markdown(incident_id=incident_id)
    assert md.strip()
    assert incident_id in md
    assert "After-Action Report" in md
    assert "Dispatch Summary" in md
    assert "Escalations" in md
    assert "Explainability" in md


def test_to_dict_is_json_serialisable() -> None:
    """The dict view round-trips through JSON (it is a wire artefact)."""
    loop = scenarios.run_scenario("A").loop
    d = IncidentReporter(bus=loop.bus, logger=loop.logger).to_dict()
    encoded = json.dumps(d)  # must not raise
    again = json.loads(encoded)
    assert again["message_count"] == d["message_count"]
    assert "timeline" in again and "dispatch" in again and "explainability" in again


# ----------------------------------------------------------------- filtering
def test_incident_filter_narrows_the_window() -> None:
    """Filtering by incident_id reduces the analysed message set."""
    incident_id = scenarios.earthquake.INCIDENT_ID
    loop = scenarios.simulate_earthquake()
    rep = IncidentReporter(bus=loop.bus, logger=loop.logger)

    full = rep.generate()
    scoped = rep.generate(incident_id=incident_id)
    assert scoped.incident_id == incident_id
    assert 0 < scoped.message_count <= full.message_count
    assert all(t.incident_id == incident_id for t in scoped.timeline)


# ----------------------------------------------------------------- degenerate
def test_empty_sources_produce_an_empty_but_valid_report() -> None:
    """With no bus/logger the report is well-formed and renders Markdown."""
    rep = IncidentReporter().generate()
    assert rep.message_count == 0
    assert rep.timeline == []
    assert rep.dispatch.total_orders == 0
    assert rep.escalations == []
    assert rep.explainability.predictions_logged == 0
    md = rep.to_markdown()
    assert md.strip()
    assert "After-Action Report" in md


def test_file_backed_logger_recovers_shap_from_disk(tmp_path) -> None:
    """SHAP attributions persisted to the JSONL trail are read back."""
    from disastermind.scenarios.base import build_loop

    audit = tmp_path / "audit.jsonl"
    logger = DecisionLogger(path=str(audit))
    loop = build_loop(logger=logger)
    loop = scenarios.simulate_earthquake(loop=loop)

    assert audit.exists() and audit.read_text().strip()
    rep = IncidentReporter(bus=loop.bus, logger=logger).generate()
    assert rep.explainability.predictions_logged > 0
    assert rep.explainability.models
