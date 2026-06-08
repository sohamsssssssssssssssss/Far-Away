"""Post-incident reporting package (PRD Group A, Step 9 — post-incident review).

After a disaster response winds down, the :class:`IncidentReporter` reconstructs
an *after-action report* purely from the durable audit artefacts the rest of the
system already produces:

  * the :class:`~disastermind.core.bus.MessageBus` ``history`` ring buffer (every
    inter-agent :class:`~disastermind.core.contracts.Message`), and/or
  * a :class:`~disastermind.audit.decision_log.DecisionLogger` trail (the
    hash-chained JSONL on disk, or a ``null()`` logger's in-memory list) which
    additionally carries SHAP-annotated model predictions logged via
    :meth:`DecisionLogger.log_prediction` (PRD Step 9 explainability).

The report contains an ISO-8601-ordered timeline, decision counts by
tier/type/priority, the escalation ledger (trigger + outcome), a dispatch
summary (orders + delivery channels), resource utilisation, and an
explainability section summarising any logged SHAP attributions. It renders to
both a plain ``dict`` and to Markdown, and can be filtered by ``incident_id``.

Pure standard library: no network, no heavy dependency, deterministic.
"""
from __future__ import annotations

from .reporter import (
    DecisionBreakdown,
    DispatchSummary,
    EscalationOutcome,
    ExplainabilitySummary,
    IncidentReport,
    IncidentReporter,
    ResourceUtilisation,
    TimelineEntry,
)

__all__ = [
    "IncidentReporter",
    "IncidentReport",
    "TimelineEntry",
    "DecisionBreakdown",
    "EscalationOutcome",
    "DispatchSummary",
    "ResourceUtilisation",
    "ExplainabilitySummary",
]
