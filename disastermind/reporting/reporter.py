"""After-action / post-incident report generation (PRD Step 9).

The :class:`IncidentReporter` is a *pure analyser*: it never publishes to the bus
and never mutates an agent. It reads the durable audit artefacts the platform
already emits — the bus ``history`` of :class:`Message` envelopes and a
:class:`DecisionLogger` trail — and reconstructs a structured after-action review.

It deliberately depends only on the FROZEN contracts
(:mod:`disastermind.core.contracts`) and on the public, JSON-able shapes the
Commander / dispatch router / prediction tier produce (see their module
docstrings). Everything is standard library: no network, no heavy dependency.

PRD Step 9 ("Decision Logging" + "post-incident review") asks the system to be
able to *explain itself after the fact*. Concretely this report captures:

  * timeline   — every recorded message, ISO-8601 ordered (PRD Step 9);
  * decisions  — counts by tier / message-type / priority;
  * escalations— the human-in-the-loop ledger: trigger + outcome
    (``auto_executed`` / ``approved`` / ``rejected`` / ``timeout`` / ``pending``)
    (PRD Step 7);
  * dispatch   — autonomous + approved orders and the delivery channels used
    (PRD Step 8);
  * resources  — field-team / asset utilisation drawn from resource plans and
    dispatch orders (PRD Step 4-6);
  * explainability — a roll-up of any logged SHAP attributions (PRD Step 9).
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.contracts import (
    Message,
    MessageType,
    Priority,
    Tier,
    Topic,
)

# ---------------------------------------------------------------------------
# Topic / kind constants owned by THIS package. We never edit core/contracts.py;
# these mirror the payload "kind" markers the frozen agents stamp on messages.
DISPATCH_ACK_KIND = "dispatch_ack"
ESCALATION_KIND = "escalation"
ESCALATION_REJECTED_KIND = "escalation_rejected"
PREDICTION_KIND = "prediction"

#: Topics whose producing agent holds decision authority (Tier 1/2). Used to map
#: a recorded message back to the authority tier that produced it without having
#: to import every agent class. Mirrors the TOPIC DAG in the orchestration loop.
_TOPIC_TIER: dict[str, Tier] = {
    Topic.RAW_FEED: Tier.EDGE,
    Topic.IOT_TELEMETRY: Tier.EDGE,
    Topic.DISPATCH: Tier.EDGE,  # router ACKs; commander DISPATCH handled below
    Topic.PREDICTION: Tier.SPECIALIST,
    Topic.CASCADE: Tier.SPECIALIST,
    Topic.RESOURCE_PLAN: Tier.SPECIALIST,
    Topic.ROUTING_PLAN: Tier.SPECIALIST,
    Topic.FIELD_ORDER: Tier.SPECIALIST,
    Topic.COMMANDER_REVIEW: Tier.COMMANDER,
    Topic.ESCALATION: Tier.COMMANDER,
}


# --------------------------------------------------------------------------- DTOs
@dataclass
class TimelineEntry:
    """One ISO-8601-stamped event on the incident timeline (PRD Step 9)."""

    timestamp: str
    topic: str
    sender: str
    recipient: str
    type: str
    priority: int
    summary: str
    incident_id: str | None = None
    message_id: str | None = None


@dataclass
class DecisionBreakdown:
    """Decision counts sliced by authority tier, message type and priority."""

    total: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    by_priority: dict[str, int] = field(default_factory=dict)


@dataclass
class EscalationOutcome:
    """A single human-in-the-loop escalation and how it resolved (PRD Step 7)."""

    report_id: str
    trigger: str
    summary: str
    human_only: bool
    timeout_seconds: int
    #: auto_executed | approved | rejected | timeout | pending
    outcome: str = "pending"
    detail: str = ""
    incident_id: str | None = None
    opened_at: str | None = None
    resolved_at: str | None = None


@dataclass
class DispatchSummary:
    """Autonomous + approved dispatch orders and the channels used (PRD Step 8)."""

    total_orders: int = 0
    by_via: dict[str, int] = field(default_factory=dict)
    by_channel: dict[str, int] = field(default_factory=dict)
    deliveries_recorded: int = 0
    deliveries_failed: int = 0
    orders: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResourceUtilisation:
    """Field-team / asset utilisation across the response (PRD Step 4-6)."""

    teams_tasked: int = 0
    plans_issued: int = 0
    by_asset_type: dict[str, int] = field(default_factory=dict)
    tasked_team_ids: list[str] = field(default_factory=list)
    population_at_risk: int = 0


@dataclass
class ExplainabilitySummary:
    """Roll-up of logged SHAP attributions per model (PRD Step 9)."""

    predictions_logged: int = 0
    models: list[str] = field(default_factory=list)
    #: model -> {feature -> mean |attribution|}
    mean_attributions: dict[str, dict[str, float]] = field(default_factory=dict)
    #: model -> the single most-influential feature
    top_feature: dict[str, str] = field(default_factory=dict)


@dataclass
class IncidentReport:
    """The complete after-action report (PRD Step 9 post-incident review)."""

    incident_id: str | None
    generated_at: str
    window: dict[str, str | None]
    message_count: int
    timeline: list[TimelineEntry]
    decisions: DecisionBreakdown
    escalations: list[EscalationOutcome]
    dispatch: DispatchSummary
    resources: ResourceUtilisation
    explainability: ExplainabilitySummary

    # -------------------------------------------------------------- rendering
    def to_dict(self) -> dict[str, Any]:
        """A fully JSON-able view of the report."""
        return {
            "incident_id": self.incident_id,
            "generated_at": self.generated_at,
            "window": dict(self.window),
            "message_count": self.message_count,
            "timeline": [asdict(t) for t in self.timeline],
            "decisions": asdict(self.decisions),
            "escalations": [asdict(e) for e in self.escalations],
            "dispatch": asdict(self.dispatch),
            "resources": asdict(self.resources),
            "explainability": asdict(self.explainability),
        }

    def to_markdown(self) -> str:
        """Render the report as a human-readable Markdown after-action review."""
        scope = self.incident_id or "ALL INCIDENTS"
        L: list[str] = []
        L.append("# DisasterMind After-Action Report")
        L.append("")
        L.append(f"- **Incident:** {scope}")
        L.append(f"- **Generated:** {self.generated_at}")
        L.append(
            f"- **Window:** {self.window.get('start') or 'n/a'}"
            f" -> {self.window.get('end') or 'n/a'}"
        )
        L.append(f"- **Messages analysed:** {self.message_count}")
        L.append("")

        # --- Decisions -----------------------------------------------------
        L.append("## Decision Summary")
        L.append("")
        L.append(f"Total recorded decisions/messages: **{self.decisions.total}**")
        L.append("")
        L.append("| Slice | Key | Count |")
        L.append("| --- | --- | --- |")
        for key, val in self.decisions.by_tier.items():
            L.append(f"| tier | {key} | {val} |")
        for key, val in self.decisions.by_type.items():
            L.append(f"| type | {key} | {val} |")
        for key, val in self.decisions.by_priority.items():
            L.append(f"| priority | {key} | {val} |")
        L.append("")

        # --- Escalations ---------------------------------------------------
        L.append("## Escalations (Human-in-the-Loop)")
        L.append("")
        if not self.escalations:
            L.append("_No escalations were raised — all decisions stayed within "
                     "autonomous authority._")
        else:
            L.append("| Report | Trigger | Outcome | Human-only | Detail |")
            L.append("| --- | --- | --- | --- | --- |")
            for e in self.escalations:
                L.append(
                    f"| {e.report_id} | {e.trigger} | **{e.outcome}** | "
                    f"{'yes' if e.human_only else 'no'} | {e.detail or ''} |"
                )
        L.append("")

        # --- Dispatch ------------------------------------------------------
        d = self.dispatch
        L.append("## Dispatch Summary")
        L.append("")
        L.append(f"- Orders dispatched: **{d.total_orders}**")
        L.append(
            f"- Deliveries recorded: {d.deliveries_recorded}"
            f" (failed: {d.deliveries_failed})"
        )
        if d.by_via:
            vias = ", ".join(f"{k}={v}" for k, v in d.by_via.items())
            L.append(f"- By authorisation path: {vias}")
        if d.by_channel:
            chans = ", ".join(f"{k}={v}" for k, v in d.by_channel.items())
            L.append(f"- By channel: {chans}")
        L.append("")

        # --- Resources -----------------------------------------------------
        r = self.resources
        L.append("## Resource Utilisation")
        L.append("")
        L.append(f"- Resource/field plans issued: {r.plans_issued}")
        L.append(f"- Teams tasked: **{r.teams_tasked}**")
        if r.population_at_risk:
            L.append(f"- Population at risk (peak forecast): {r.population_at_risk}")
        if r.by_asset_type:
            assets = ", ".join(f"{k}={v}" for k, v in r.by_asset_type.items())
            L.append(f"- By asset type: {assets}")
        if r.tasked_team_ids:
            L.append(f"- Tasked teams: {', '.join(r.tasked_team_ids)}")
        L.append("")

        # --- Explainability ------------------------------------------------
        x = self.explainability
        L.append("## Explainability (SHAP)")
        L.append("")
        if x.predictions_logged == 0:
            L.append("_No model predictions with SHAP attributions were logged._")
        else:
            L.append(f"- Predictions logged: **{x.predictions_logged}**")
            L.append(f"- Models: {', '.join(x.models) or 'n/a'}")
            for model, feats in x.mean_attributions.items():
                top = x.top_feature.get(model, "")
                ranked = ", ".join(
                    f"{k}={v}" for k, v in sorted(
                        feats.items(), key=lambda kv: kv[1], reverse=True
                    )
                )
                L.append(f"  - **{model}** (top driver: {top}): {ranked}")
        L.append("")

        # --- Timeline ------------------------------------------------------
        L.append("## Timeline")
        L.append("")
        if not self.timeline:
            L.append("_No messages in window._")
        else:
            for t in self.timeline:
                L.append(
                    f"- `{t.timestamp}` **{t.topic}** "
                    f"({t.sender} -> {t.recipient}): {t.summary}"
                )
        L.append("")
        return "\n".join(L)


# --------------------------------------------------------------------------- core
class IncidentReporter:
    """Builds an :class:`IncidentReport` from a bus and/or a decision logger.

    Parameters
    ----------
    bus:
        Any object exposing a ``history`` list of :class:`Message` (the
        :class:`~disastermind.core.bus.InMemoryBus` / Kafka fallback). Optional.
    logger:
        A :class:`~disastermind.audit.decision_log.DecisionLogger`. We read its
        in-memory ``memory`` list (``null()`` logger) and/or re-read its on-disk
        JSONL trail to recover SHAP-annotated predictions. Optional.

    At least one source should be supplied; with neither, the report is empty.
    """

    def __init__(self, bus: Any = None, logger: Any = None) -> None:
        self.bus = bus
        self.logger = logger

    # -------------------------------------------------------- public entry point
    def generate(self, incident_id: str | None = None) -> IncidentReport:
        """Produce the after-action report, optionally filtered by ``incident_id``."""
        from ..core.contracts import utcnow_iso

        messages = self._collect_messages(incident_id)
        records = self._collect_logger_records(incident_id)

        timeline = self._build_timeline(messages)
        decisions = self._build_decisions(messages)
        escalations = self._build_escalations(messages)
        dispatch = self._build_dispatch(messages)
        resources = self._build_resources(messages)
        explainability = self._build_explainability(records)

        window = {
            "start": timeline[0].timestamp if timeline else None,
            "end": timeline[-1].timestamp if timeline else None,
        }
        return IncidentReport(
            incident_id=incident_id,
            generated_at=utcnow_iso(),
            window=window,
            message_count=len(messages),
            timeline=timeline,
            decisions=decisions,
            escalations=escalations,
            dispatch=dispatch,
            resources=resources,
            explainability=explainability,
        )

    # convenience wrappers ---------------------------------------------------
    def to_dict(self, incident_id: str | None = None) -> dict[str, Any]:
        """Shortcut: generate and return the dict view."""
        return self.generate(incident_id).to_dict()

    def to_markdown(self, incident_id: str | None = None) -> str:
        """Shortcut: generate and return the Markdown view."""
        return self.generate(incident_id).to_markdown()

    # --------------------------------------------------------------- collection
    def _collect_messages(self, incident_id: str | None) -> list[Message]:
        """Pull bus-history messages, sorted by ISO timestamp, optionally filtered."""
        history = list(getattr(self.bus, "history", []) or [])
        msgs = [m for m in history if isinstance(m, Message)]
        if incident_id is not None:
            msgs = [m for m in msgs if m.incident_id == incident_id]
        # Stable ISO-8601 ordering (timestamps are RFC3339-comparable strings).
        msgs.sort(key=lambda m: (m.timestamp or "", m.id or ""))
        return msgs

    def _collect_logger_records(self, incident_id: str | None) -> list[dict[str, Any]]:
        """Recover raw logger records (in-memory list and/or on-disk JSONL)."""
        records: list[dict[str, Any]] = []
        logger = self.logger
        if logger is not None:
            mem = getattr(logger, "memory", None)
            if isinstance(mem, list):
                records.extend(r for r in mem if isinstance(r, dict))
            path = getattr(logger, "path", "") or ""
            if path and os.path.exists(path):
                records.extend(self._read_jsonl(path))
        if incident_id is not None:
            records = [r for r in records if r.get("incident_id") == incident_id]
        return records

    @staticmethod
    def _read_jsonl(path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except Exception:
            return out
        return out

    # ------------------------------------------------------------------ sections
    def _build_timeline(self, messages: list[Message]) -> list[TimelineEntry]:
        entries: list[TimelineEntry] = []
        for m in messages:
            entries.append(
                TimelineEntry(
                    timestamp=m.timestamp,
                    topic=m.topic,
                    sender=m.sender,
                    recipient=m.recipient,
                    type=self._type_value(m.type),
                    priority=int(m.priority),
                    summary=self._summarise(m),
                    incident_id=m.incident_id,
                    message_id=m.id,
                )
            )
        return entries

    def _build_decisions(self, messages: list[Message]) -> DecisionBreakdown:
        by_tier: Counter[str] = Counter()
        by_type: Counter[str] = Counter()
        by_priority: Counter[str] = Counter()
        for m in messages:
            by_tier[self._tier_name(m)] += 1
            by_type[self._type_value(m.type)] += 1
            by_priority[self._priority_name(m.priority)] += 1
        return DecisionBreakdown(
            total=len(messages),
            by_tier=dict(sorted(by_tier.items())),
            by_type=dict(sorted(by_type.items())),
            by_priority=dict(sorted(by_priority.items())),
        )

    def _build_escalations(self, messages: list[Message]) -> list[EscalationOutcome]:
        """Reconstruct the escalation ledger + outcome from the message stream.

        An ``ESCALATION`` message opens a report (carrying its trigger + report).
        We then look for the resolution among later messages:
          * a rejection ACK (``kind == escalation_rejected``) -> ``rejected``;
          * a DISPATCH whose ``order`` matches the escalation's order and whose
            ``via`` marks a human approval or a timeout auto-execute;
          * otherwise the report is still ``pending``.

        If a live Commander is reachable on the bus subscribers we cannot see it
        here, so we infer purely from the recorded stream (PRD Step 7/9).
        """
        opens: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for m in messages:
            payload = m.payload or {}
            if (
                m.topic == Topic.ESCALATION
                and self._type_value(m.type) == MessageType.ESCALATION.value
                and payload.get("kind") == ESCALATION_KIND
            ):
                rid = payload.get("report_id") or ""
                report = payload.get("report") or {}
                if rid and rid not in opens:
                    order.append(rid)
                opens[rid] = {
                    "report_id": rid,
                    "trigger": (
                        m.escalation_trigger.value
                        if getattr(m.escalation_trigger, "value", None)
                        else (report.get("trigger") or "unknown")
                    ),
                    "summary": report.get("summary", ""),
                    "human_only": bool(
                        payload.get("human_only", report.get("human_only", False))
                    ),
                    "timeout_seconds": int(
                        payload.get(
                            "timeout_seconds", report.get("timeout_seconds", 0)
                        )
                    ),
                    "incident_id": m.incident_id,
                    "opened_at": m.timestamp,
                    "outcome": "pending",
                    "detail": "awaiting human decision",
                    "resolved_at": None,
                    "_order": (report.get("supporting") or {}).get("order"),
                }

        # Second pass: resolve outcomes from rejections + dispatches.
        for m in messages:
            payload = m.payload or {}
            # Human rejection ACK carries the report_id directly.
            if payload.get("kind") == ESCALATION_REJECTED_KIND:
                rid = payload.get("report_id") or ""
                rec = opens.get(rid)
                if rec is not None:
                    rec["outcome"] = "rejected"
                    rec["detail"] = (
                        f"rejected by {payload.get('approver', 'human')}"
                        + (f": {payload.get('note')}" if payload.get("note") else "")
                    )
                    rec["resolved_at"] = m.timestamp
                continue
            # A DISPATCH resolving an escalation references the original order.
            if m.topic == Topic.DISPATCH and payload.get("via"):
                via = str(payload.get("via", ""))
                dispatched_order = payload.get("order")
                if "auto_execute_on_timeout" in via:
                    rec = self._match_pending_order(opens, dispatched_order)
                    if rec is not None and rec["outcome"] == "pending":
                        rec["outcome"] = "auto_executed"
                        rec["detail"] = "auto-executed on timeout (no human response)"
                        rec["resolved_at"] = m.timestamp
                elif "human_approved" in via:
                    rec = self._match_pending_order(opens, dispatched_order)
                    if rec is not None and rec["outcome"] == "pending":
                        rec["outcome"] = "approved"
                        rec["detail"] = via.replace("human_approved:", "approved by ")
                        rec["resolved_at"] = m.timestamp

        out: list[EscalationOutcome] = []
        for rid in order:
            rec = opens[rid]
            rec.pop("_order", None)
            out.append(EscalationOutcome(**rec))
        return out

    @staticmethod
    def _match_pending_order(
        opens: dict[str, dict[str, Any]], dispatched_order: Any
    ) -> dict[str, Any] | None:
        """Find a still-pending escalation whose supporting order matches."""
        if not isinstance(dispatched_order, dict):
            return None
        for rec in opens.values():
            if rec["outcome"] != "pending":
                continue
            ord_ = rec.get("_order")
            if isinstance(ord_, dict) and ord_ == dispatched_order:
                return rec
        # Fall back to a team_id match if dicts are not byte-identical.
        team = dispatched_order.get("team_id")
        if team is not None:
            for rec in opens.values():
                if rec["outcome"] != "pending":
                    continue
                ord_ = rec.get("_order")
                if isinstance(ord_, dict) and ord_.get("team_id") == team:
                    return rec
        return None

    def _build_dispatch(self, messages: list[Message]) -> DispatchSummary:
        summary = DispatchSummary()
        by_via: Counter[str] = Counter()
        by_channel: Counter[str] = Counter()
        for m in messages:
            if m.topic != Topic.DISPATCH:
                continue
            payload = m.payload or {}
            kind = payload.get("kind")
            if kind == DISPATCH_ACK_KIND:
                # Delivery ACK from the Tier-3 router (PRD Step 8).
                summary.deliveries_recorded += int(payload.get("delivered", 0) or 0)
                summary.deliveries_failed += int(payload.get("failed", 0) or 0)
                continue
            if self._type_value(m.type) == MessageType.ACK.value:
                continue  # any other housekeeping ACK
            # A real dispatch order from the Commander (Tier 1).
            summary.total_orders += 1
            via = str(payload.get("via", "autonomous"))
            by_via[via] += 1
            channel = payload.get("channel", "field_radio")
            for ch in self._channel_names(channel):
                by_channel[ch] += 1
            summary.orders.append(
                {
                    "team_id": (payload.get("order") or {}).get("team_id"),
                    "channel": channel,
                    "via": via,
                    "body": payload.get("body", ""),
                    "incident_id": m.incident_id,
                    "timestamp": m.timestamp,
                }
            )
        summary.by_via = dict(sorted(by_via.items()))
        summary.by_channel = dict(sorted(by_channel.items()))
        return summary

    def _build_resources(self, messages: list[Message]) -> ResourceUtilisation:
        plans = 0
        teams: set[str] = set()
        asset_types: Counter[str] = Counter()
        peak_pop = 0
        for m in messages:
            payload = m.payload or {}
            if m.topic in (Topic.RESOURCE_PLAN, Topic.ROUTING_PLAN, Topic.FIELD_ORDER):
                if self._type_value(m.type) != MessageType.ACK.value:
                    plans += 1
                for tid, atype in self._iter_assignments(payload):
                    if tid:
                        teams.add(str(tid))
                    if atype:
                        asset_types[str(atype)] += 1
                peak_pop = max(peak_pop, self._population_at_risk(payload))
            elif m.topic == Topic.DISPATCH and payload.get("via"):
                tid = (payload.get("order") or {}).get("team_id")
                if tid:
                    teams.add(str(tid))
            elif m.topic == Topic.PREDICTION:
                peak_pop = max(peak_pop, self._population_at_risk(payload))
        return ResourceUtilisation(
            teams_tasked=len(teams),
            plans_issued=plans,
            by_asset_type=dict(sorted(asset_types.items())),
            tasked_team_ids=sorted(teams),
            population_at_risk=peak_pop,
        )

    def _build_explainability(
        self, records: list[dict[str, Any]]
    ) -> ExplainabilitySummary:
        preds = [r for r in records if r.get("kind") == PREDICTION_KIND]
        accum: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        models: list[str] = []
        for r in preds:
            model = str(r.get("model", "unknown"))
            if model not in models:
                models.append(model)
            shap = r.get("shap") or {}
            if isinstance(shap, dict):
                for feature, value in shap.items():
                    try:
                        accum[model][str(feature)].append(abs(float(value)))
                    except (TypeError, ValueError):
                        continue
        mean_attr: dict[str, dict[str, float]] = {}
        top_feature: dict[str, str] = {}
        for model, feats in accum.items():
            means = {
                feat: round(sum(vals) / len(vals), 4)
                for feat, vals in feats.items()
                if vals
            }
            mean_attr[model] = dict(
                sorted(means.items(), key=lambda kv: kv[1], reverse=True)
            )
            if means:
                top_feature[model] = max(means.items(), key=lambda kv: kv[1])[0]
        return ExplainabilitySummary(
            predictions_logged=len(preds),
            models=models,
            mean_attributions=mean_attr,
            top_feature=top_feature,
        )

    # --------------------------------------------------------------- payload aids
    def _iter_assignments(self, payload: dict[str, Any]):
        """Yield ``(team_id, asset_type)`` pairs from a resource/field payload.

        The frozen Tier-2 agents use several near-identical container keys
        (``orders`` / ``assignments`` / ``allocations`` / ``zones``); we probe
        them all defensively so the report works across modules A/B/C. When an
        order carries no explicit asset-type field we infer it from the team-id
        prefix (e.g. ``NDRF-01`` -> ``ndrf``), matching the roster convention in
        :mod:`disastermind.scenarios.base`.
        """
        for key in ("orders", "assignments", "allocations", "deployments", "tasks"):
            items = payload.get(key)
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                tid = it.get("team_id") or it.get("asset_id") or it.get("id")
                atype = (
                    it.get("asset_type")
                    or it.get("type")
                    or it.get("resource_type")
                    or self._infer_asset_type(tid)
                )
                yield tid, atype

    @staticmethod
    def _infer_asset_type(team_id: Any) -> str | None:
        """Best-effort asset class from a team-id prefix (``NDRF-01`` -> ``ndrf``)."""
        if not isinstance(team_id, str) or "-" not in team_id:
            return None
        prefix = team_id.split("-", 1)[0].strip().lower()
        return prefix or None

    @staticmethod
    def _population_at_risk(payload: dict[str, Any]) -> int:
        """Best-effort peak population-at-risk reading from a payload."""
        for key in ("population_at_risk", "total_population_at_risk"):
            val = payload.get(key)
            if isinstance(val, (int, float)):
                return int(val)
        cells = payload.get("risk_cells")
        if isinstance(cells, list):
            total = 0
            for c in cells:
                if isinstance(c, dict):
                    p = c.get("population_at_risk")
                    if isinstance(p, (int, float)):
                        total += int(p)
            if total:
                return total
        return 0

    @staticmethod
    def _channel_names(channel: Any) -> list[str]:
        if channel in (None, "", "all", "*", "broadcast_all"):
            return [str(channel) if channel else "all"]
        if isinstance(channel, (list, tuple, set)):
            return [str(c) for c in channel]
        return [str(channel)]

    # --------------------------------------------------------------- formatting
    def _summarise(self, m: Message) -> str:
        payload = m.payload or {}
        kind = payload.get("kind")
        if m.topic == Topic.ESCALATION and kind == ESCALATION_KIND:
            trig = (
                m.escalation_trigger.value
                if getattr(m.escalation_trigger, "value", None)
                else "escalation"
            )
            return f"ESCALATION raised: {trig} ({payload.get('report_id', '')})"
        if kind == ESCALATION_REJECTED_KIND:
            return f"escalation {payload.get('report_id', '')} rejected"
        if kind == DISPATCH_ACK_KIND:
            return (
                f"delivery ack: {payload.get('delivered', 0)} sent, "
                f"{payload.get('failed', 0)} failed"
            )
        if m.topic == Topic.DISPATCH and payload.get("body"):
            return str(payload["body"])
        if m.reasoning:
            return str(m.reasoning[0])
        return f"{self._type_value(m.type)} on {m.topic}"

    @staticmethod
    def _type_value(t: Any) -> str:
        return t.value if isinstance(t, MessageType) else str(t)

    @staticmethod
    def _priority_name(p: Any) -> str:
        try:
            return Priority(int(p)).name
        except (ValueError, TypeError):
            return str(p)

    def _tier_name(self, m: Message) -> str:
        """Map a recorded message to the authority tier that produced it."""
        payload = m.payload or {}
        # A Commander DISPATCH carries a ``via`` marker; router ACKs do not.
        if m.topic == Topic.DISPATCH:
            if payload.get("kind") == DISPATCH_ACK_KIND or (
                self._type_value(m.type) == MessageType.ACK.value
            ):
                return Tier.EDGE.name
            return Tier.COMMANDER.name
        tier = _TOPIC_TIER.get(m.topic)
        return tier.name if tier is not None else "UNKNOWN"
