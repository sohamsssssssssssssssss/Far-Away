"""The narrated golden-path runner (see package docstring).

:func:`run_demo` drives one disaster module end to end and returns a
:class:`DemoTranscript` capturing every stage. Everything is offline, stdlib-only
and deterministic: the scenario generators inject fixed synthetic signals and
drive the loop with a frozen clock (``clock=lambda: 0.0``), the reporter and the
``TemplateClient``-backed advisor render reproducible text, and no pseudo-random
state is used anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from typing import Any, Callable

from ..core.contracts import Module, Topic
from ..orchestration.triggers import Signals, should_activate
from .. import scenarios as _scenarios
from ..reporting import IncidentReporter
from ..llm import DecisionSupportAdvisor, TemplateClient
from ..models.domain import DisasterEvent, EventKind
from ..models.geo import LatLon

# --------------------------------------------------------------------------- config
# Per-module wiring: the scenario generator to drive, its module enum, the stable
# INCIDENT_ID it stamps, a human label, and a synthetic activation Signals snapshot
# crafted so orchestration.triggers.should_activate returns exactly this module.
# (Signals are reconstructed here only to *narrate* activation; the scenario itself
# injects the real hazard event that drives the pipeline.)
_DISPATCH_ACK_KIND = "dispatch_ack"  # mirrors the marker frozen agents stamp on ACKs


@dataclass(frozen=True)
class _ModuleWiring:
    module: Module
    simulate: Callable[..., Any]
    incident_id: str
    label: str
    signals: Signals
    is_cyclone: bool = False


def _wiring() -> dict[str, _ModuleWiring]:
    """Build the per-module wiring table from the stable scenarios package."""
    return {
        "A": _ModuleWiring(
            module=Module.CYCLONE_FLOOD,
            simulate=_scenarios.simulate_cyclone_flood,
            incident_id=_scenarios.cyclone_flood.INCIDENT_ID,
            label="Cyclone / Flood",
            # IMD cyclone alert + gauge past danger -> module A activates.
            signals=Signals(imd_cyclone_alert=True, river_gauge_pct_of_danger=130.0),
            is_cyclone=True,
        ),
        "B": _ModuleWiring(
            module=Module.EARTHQUAKE,
            simulate=_scenarios.simulate_earthquake,
            incident_id=_scenarios.earthquake.INCIDENT_ID,
            label="Earthquake",
            # M6.2 >= M4.5 activation threshold -> module B activates.
            signals=Signals(max_seismic_magnitude=6.2),
        ),
        "C": _ModuleWiring(
            module=Module.FIRE_COLLAPSE,
            simulate=_scenarios.simulate_urban_fire,
            incident_id=_scenarios.urban_fire.INCIDENT_ID,
            label="Urban Fire / Collapse",
            # FIRMS anomaly + brigade-call cluster -> module C activates.
            signals=Signals(
                fire_brigade_calls_in_zone_10min=4, firms_thermal_anomaly=True
            ),
        ),
    }


DEMO_MODULES: tuple[str, ...] = ("A", "B", "C")

# Topics we tally for the pipeline narrative, in load-bearing chain order.
_CHAIN_TOPICS: tuple[str, ...] = (
    Topic.RAW_FEED,
    Topic.PREDICTION,
    Topic.CASCADE,
    Topic.RESOURCE_PLAN,
    Topic.ROUTING_PLAN,
    Topic.FIELD_ORDER,
    Topic.COMMANDER_REVIEW,
    Topic.ESCALATION,
    Topic.DISPATCH,
)


# --------------------------------------------------------------------------- result
@dataclass
class DemoTranscript:
    """A narrated, JSON-able record of one end-to-end demo run.

    Behaves like a ``dict`` for the fields callers commonly probe (``module``,
    ``activation``, ``tally``, ``report``, ``brief``, ``public_alert``) and also
    renders to Markdown.
    """

    module: str
    label: str
    incident_id: str
    escalate: bool
    activation: dict[str, Any]
    tally: dict[str, Any]
    report: dict[str, Any]
    brief: str
    public_alert: list[dict[str, Any]] = field(default_factory=list)
    narrative: list[str] = field(default_factory=list)

    # -- dict-like access so scripts can do transcript["tally"], etc. ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "label": self.label,
            "incident_id": self.incident_id,
            "escalate": self.escalate,
            "activation": self.activation,
            "tally": self.tally,
            "report": self.report,
            "brief": self.brief,
            "public_alert": self.public_alert,
            "narrative": list(self.narrative),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def keys(self):
        return self.to_dict().keys()

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()

    # -- rendering -------------------------------------------------------------
    def to_markdown(self) -> str:
        L: list[str] = []
        L.append(f"# DisasterMind Demo — {self.label} (module {self.module})")
        L.append("")
        L.append(f"- **Incident:** {self.incident_id}")
        L.append(f"- **Escalation path:** {'yes' if self.escalate else 'no'}")
        L.append("")

        # 1. Activation -------------------------------------------------------
        L.append("## 1. Activation")
        L.append("")
        act = self.activation
        verdict = "ACTIVATED" if act.get("activated") else "not activated"
        L.append(
            f"orchestration.triggers.should_activate -> **{act.get('decided') or 'none'}** "
            f"({verdict})"
        )
        L.append("")

        # 2. Pipeline tally ---------------------------------------------------
        L.append("## 2. Pipeline (loop.bus.history)")
        L.append("")
        L.append(f"Messages on the bus: **{self.tally.get('message_count', 0)}**")
        L.append("")
        L.append("| Topic | Count |")
        L.append("| --- | --- |")
        for topic in _CHAIN_TOPICS:
            L.append(f"| {topic} | {self.tally.get('topics', {}).get(topic, 0)} |")
        L.append("")
        L.append(
            f"Dispatched (non-ack): **{self.tally.get('dispatch', 0)}**  ·  "
            f"Escalations: **{self.tally.get('escalation', 0)}**"
        )
        L.append("")

        # 3. After-action report ---------------------------------------------
        L.append("## 3. After-Action Report")
        L.append("")
        L.append(self.report.get("markdown", "_(no report)_"))
        L.append("")

        # 4. Situation brief --------------------------------------------------
        L.append("## 4. Commander Situation Brief")
        L.append("")
        L.append("```")
        L.append(self.brief)
        L.append("```")
        L.append("")

        # 5. Public alert (cyclone only) -------------------------------------
        if self.public_alert:
            L.append("## 5. Public Alert")
            L.append("")
            for a in self.public_alert:
                L.append(f"**[{a['language']}] {a['headline']}**")
                L.append("")
                L.append(a["body"])
                L.append("")

        return "\n".join(L)

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.to_markdown()


# --------------------------------------------------------------------------- helpers
def _tally_history(history: list[Any]) -> dict[str, Any]:
    """Count messages by topic and surface DISPATCH/ESCALATION from the bus."""
    topics: Counter[str] = Counter(m.topic for m in history)
    # A *real* dispatch (not a router ACK) — mirrors the advisor's own rule.
    dispatch = sum(
        1
        for m in history
        if m.topic == Topic.DISPATCH
        and (getattr(m, "payload", None) or {}).get("kind") != _DISPATCH_ACK_KIND
    )
    escalation = topics.get(Topic.ESCALATION, 0)
    return {
        "message_count": len(history),
        "topics": dict(topics),
        "dispatch": dispatch,
        "escalation": escalation,
    }


def _event_from_history(history: list[Any], incident_id: str) -> DisasterEvent | None:
    """Reconstruct the injected DisasterEvent from the RAW_FEED message.

    Used to draft a public alert for a cyclone. Returns ``None`` if no scenario
    signal is present (so callers degrade gracefully rather than crash).
    """
    for m in history:
        if m.topic != Topic.RAW_FEED:
            continue
        payload = getattr(m, "payload", None) or {}
        ev = payload.get("event")
        if not isinstance(ev, dict):
            continue
        if incident_id is not None and ev.get("incident_id") != incident_id:
            continue
        epi = ev.get("epicentre") or {}
        try:
            kind = EventKind(ev.get("kind"))
        except ValueError:
            continue
        return DisasterEvent(
            incident_id=ev.get("incident_id", incident_id),
            kind=kind,
            epicentre=LatLon(float(epi.get("lat", 0.0)), float(epi.get("lon", 0.0))),
            severity=float(ev.get("severity", 0.0)),
            detected_at=ev.get("detected_at", ""),
            source=ev.get("source", ""),
            meta=dict(ev.get("meta", {})),
        )
    return None


# --------------------------------------------------------------------------- runner
def run_demo(module: str = "B", escalate: bool = False) -> DemoTranscript:
    """Drive one disaster module end to end and return a narrated transcript.

    Parameters
    ----------
    module:
        ``"A"`` (cyclone/flood), ``"B"`` (earthquake), or ``"C"`` (urban fire).
        Case-insensitive.
    escalate:
        Passed through to the scenario generator; when ``True`` the scenario
        injects a human-approval order so a ``Topic.ESCALATION`` surfaces in the
        tally.
    """
    key = (module or "B").strip().upper()
    table = _wiring()
    if key not in table:
        raise ValueError(
            f"unknown demo module {module!r}; expected one of {DEMO_MODULES}"
        )
    w = table[key]
    narrative: list[str] = []

    # (1) Activation — narrate orchestration.triggers.should_activate ----------
    decided = should_activate(w.signals)
    activated = decided == w.module
    activation = {
        "module": w.module.value,
        "decided": decided.value if decided is not None else None,
        "activated": bool(activated),
        "lead_time_note": _LEAD_NOTES[w.module],
    }
    narrative.append(
        f"should_activate -> {activation['decided']} "
        f"({'activated' if activated else 'no activation'})"
    )

    # (2) Drive the matching scenario (frozen clock, fully deterministic) -------
    loop = w.simulate(escalate=escalate)
    history = list(loop.bus.history)
    narrative.append(f"drove {w.label} scenario: {len(history)} bus messages")

    # (3) Tally topic counts + DISPATCH / ESCALATION ---------------------------
    tally = _tally_history(history)
    narrative.append(
        f"tally: {tally['dispatch']} dispatch(es), {tally['escalation']} escalation(s)"
    )

    # (4) After-action report --------------------------------------------------
    report_obj = IncidentReporter(loop.bus).generate(w.incident_id)
    report = {
        "dict": report_obj.to_dict(),
        "markdown": report_obj.to_markdown(),
    }
    narrative.append("generated after-action report")

    # (5) Situation brief (+ public alert for a cyclone) -----------------------
    advisor = DecisionSupportAdvisor(client=TemplateClient())
    brief = advisor.situation_brief(loop.bus, w.incident_id)
    narrative.append("composed commander situation brief")

    public_alert: list[dict[str, Any]] = []
    if w.is_cyclone:
        event = _event_from_history(history, w.incident_id)
        if event is not None:
            alerts = advisor.draft_public_alert(event)
            public_alert = [a.to_dict() for a in alerts]
            narrative.append(f"drafted public alert in {len(public_alert)} language(s)")

    return DemoTranscript(
        module=key,
        label=w.label,
        incident_id=w.incident_id,
        escalate=bool(escalate),
        activation=activation,
        tally=tally,
        report=report,
        brief=brief,
        public_alert=public_alert,
        narrative=narrative,
    )


# Lead-time notes keyed by module (static narration, mirrors triggers precedence).
_LEAD_NOTES: dict[Module, str] = {
    Module.EARTHQUAKE: "activate within 90 seconds of detection",
    Module.FIRE_COLLAPSE: "activate immediately on threshold breach",
    Module.CYCLONE_FLOOD: "activate 72 hours before projected landfall",
}
