"""Group B decision-support advisor (PRD Step 7 — human decision support).

Beyond the :class:`~disastermind.llm.narrator.EscalationNarrator` (which reacts to
escalations), the :class:`DecisionSupportAdvisor` is an *on-demand* analyst the
human commander can call:

  * :meth:`situation_brief`        — a concise read of the live incident from the bus.
  * :meth:`recommend_reallocation` — advisory asset moves to cover resource gaps.
  * :meth:`draft_public_alert`     — multi-language public alert copy.

It is **advisory only**: it never emits on the bus and never acts autonomously —
it informs a human. It follows the established LLM pattern: a fully-formed,
deterministic text body is built here and passed through
:meth:`LLMClient.generate`, so the offline :class:`TemplateClient` yields a clear,
reproducible result with no network (PRD Step 10). Public alert copy is built
purely from deterministic templates (never an LLM) because alert wording is
safety-critical.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..core.config import Settings
from ..core.contracts import Message, Topic
from ..models.domain import DisasterEvent, EventKind
from ..models.geo import LatLon, haversine
from .client import LLMClient, make_client


# --------------------------------------------------------------------------- types
@dataclass
class ReallocationMove:
    asset_id: str
    asset_type: str
    to_zone: str
    distance_km: float
    reason: str


@dataclass
class ReallocationAdvice:
    moves: list[ReallocationMove] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)  # zones with no spare asset
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "moves": [vars(m) for m in self.moves],
            "uncovered": self.uncovered,
            "rationale": self.rationale,
        }


@dataclass
class PublicAlert:
    language: str
    headline: str
    body: str

    def to_dict(self) -> dict:
        return {"language": self.language, "headline": self.headline, "body": self.body}


def _history(source) -> list[Message]:
    """Accept a bus (with ``.history``) or a raw list of messages."""
    if hasattr(source, "history"):
        return list(source.history)
    return list(source or [])


# ----------------------------------------------------------------------- advisor
class DecisionSupportAdvisor:
    def __init__(self, client: LLMClient | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.client = client or make_client(self.settings)

    # ----------------------------------------------------------- situation brief
    def situation_brief(self, source, incident_id: str | None = None) -> str:
        """Summarise the live incident from the bus history (PRD Step 7)."""
        msgs = _history(source)
        if incident_id is not None:
            msgs = [m for m in msgs if m.incident_id == incident_id]

        topics = Counter(m.topic for m in msgs)
        modules = Counter(m.module.value for m in msgs if m.module)
        triggers = Counter(
            m.escalation_trigger.value for m in msgs if m.escalation_trigger is not None
        )
        dispatches = sum(
            1
            for m in msgs
            if m.topic == Topic.DISPATCH and (m.payload or {}).get("kind") != "dispatch_ack"
        )
        escalations = topics.get(Topic.ESCALATION, 0)

        lines = [
            "DISASTERMIND SITUATION BRIEF",
            f"incident: {incident_id or 'ALL'}",
            f"messages observed: {len(msgs)}",
            "active modules: "
            + (", ".join(f"{k}×{v}" for k, v in modules.most_common()) or "none"),
            f"predictions: {topics.get(Topic.PREDICTION, 0)}  "
            f"cascades: {topics.get(Topic.CASCADE, 0)}  "
            f"resource plans: {topics.get(Topic.RESOURCE_PLAN, 0)}  "
            f"routing plans: {topics.get(Topic.ROUTING_PLAN, 0)}",
            f"field orders: {topics.get(Topic.FIELD_ORDER, 0)}  "
            f"dispatched: {dispatches}  escalations: {escalations}",
        ]
        if triggers:
            lines.append(
                "escalation triggers: "
                + ", ".join(f"{k}×{v}" for k, v in triggers.most_common())
            )
        lines.append(
            "ASSESSMENT: "
            + (
                "autonomous response active; "
                if dispatches
                else "no autonomous dispatch yet; "
            )
            + (
                f"{escalations} decision(s) await human review."
                if escalations
                else "no decisions awaiting human review."
            )
        )
        # Pass through the LLM (echoed verbatim offline) so a real model can
        # tighten the prose while the offline path stays deterministic.
        return self.client.generate("\n".join(lines))

    # ------------------------------------------------------- reallocation advice
    def recommend_reallocation(
        self,
        gaps: list[dict],
        spare_assets: list[dict],
        *,
        zone_locations: dict[str, dict] | None = None,
    ) -> ReallocationAdvice:
        """Advise moving the nearest spare asset of the right type into each gap.

        ``gaps``: ``[{"zone_id","asset_type","shortfall", "location"?}]``.
        ``spare_assets``: ``[{"asset_id","type","location":{lat,lon},"available"}]``.
        Greedy nearest-match; advisory only — emits nothing (PRD Step 7).
        """
        zone_locations = zone_locations or {}
        pool = [a for a in spare_assets if a.get("available", True)]
        used: set[str] = set()
        moves: list[ReallocationMove] = []
        uncovered: list[str] = []

        for gap in gaps:
            zone = gap.get("zone_id", "?")
            want = gap.get("type") or gap.get("asset_type")
            loc = gap.get("location") or zone_locations.get(zone)
            origin = LatLon(float(loc["lat"]), float(loc["lon"])) if loc else None

            candidates = [
                a for a in pool if a["asset_id"] not in used and (want is None or a.get("type") == want)
            ]
            if not candidates:
                uncovered.append(zone)
                continue

            if origin is not None:
                candidates.sort(
                    key=lambda a: haversine(
                        origin, LatLon(a["location"]["lat"], a["location"]["lon"])
                    )
                )
                dist_km = round(
                    haversine(
                        origin,
                        LatLon(candidates[0]["location"]["lat"], candidates[0]["location"]["lon"]),
                    )
                    / 1000.0,
                    1,
                )
            else:
                dist_km = -1.0
            pick = candidates[0]
            used.add(pick["asset_id"])
            moves.append(
                ReallocationMove(
                    asset_id=pick["asset_id"],
                    asset_type=pick.get("type", want or "?"),
                    to_zone=zone,
                    distance_km=dist_km,
                    reason=f"cover shortfall of {gap.get('shortfall', 1)} {want or 'unit'}(s) in {zone}",
                )
            )

        body = ["DISASTERMIND REALLOCATION ADVICE (advisory — requires human authorisation)"]
        for m in moves:
            d = f"{m.distance_km}km" if m.distance_km >= 0 else "n/a"
            body.append(f"- move {m.asset_id} ({m.asset_type}) -> {m.to_zone} [{d}]: {m.reason}")
        if uncovered:
            body.append("UNCOVERED (no spare asset available): " + ", ".join(uncovered))
        rationale = self.client.generate("\n".join(body))
        return ReallocationAdvice(moves=moves, uncovered=uncovered, rationale=rationale)

    # ----------------------------------------------------------- public alerting
    #: deterministic, safety-critical templates — never routed through an LLM.
    _HAZARD = {
        EventKind.CYCLONE: {"en": "cyclone", "hi": "चक्रवात", "or": "ବାତ୍ୟା"},
        EventKind.FLOOD: {"en": "flood", "hi": "बाढ़", "or": "ବନ୍ୟା"},
        EventKind.EARTHQUAKE: {"en": "earthquake", "hi": "भूकंप", "or": "ଭୂମିକମ୍ପ"},
        EventKind.URBAN_FIRE: {"en": "fire", "hi": "आग", "or": "ନିଆଁ"},
        EventKind.STRUCTURAL_COLLAPSE: {
            "en": "building collapse",
            "hi": "इमारत गिरना",
            "or": "କୋଠା ଭାଙ୍ଗିବା",
        },
    }
    _ACTION = {  # evacuate vs shelter-in-place by hazard
        EventKind.CYCLONE: "evacuate",
        EventKind.FLOOD: "evacuate",
        EventKind.URBAN_FIRE: "evacuate",
        EventKind.STRUCTURAL_COLLAPSE: "evacuate",
        EventKind.EARTHQUAKE: "shelter",  # drop-cover-hold, then move to open ground
    }
    _COPY = {
        "en": {
            "head": "ALERT: {hazard} near {place}",
            "evacuate": "Evacuate now to the nearest shelter. Avoid flooded/blocked roads.",
            "shelter": "Drop, cover, hold. After shaking, move to open ground away from buildings.",
            "tail": "Follow official instructions. — DisasterMind / NDMA",
        },
        "hi": {
            "head": "चेतावनी: {place} के पास {hazard}",
            "evacuate": "तुरंत निकटतम शरण स्थल पर जाएँ। बाढ़ग्रस्त/अवरुद्ध सड़कों से बचें।",
            "shelter": "झुकें, ढकें, पकड़ें। झटकों के बाद इमारतों से दूर खुले स्थान पर जाएँ।",
            "tail": "आधिकारिक निर्देशों का पालन करें। — DisasterMind / NDMA",
        },
        "or": {
            "head": "ସତର୍କତା: {place} ନିକଟରେ {hazard}",
            "evacuate": "ବର୍ତ୍ତମାନ ନିକଟତମ ଆଶ୍ରୟସ୍ଥଳକୁ ଯାଆନ୍ତୁ। ବନ୍ୟା/ଅବରୁଦ୍ଧ ରାସ୍ତାରୁ ଦୂରେ ରୁହନ୍ତୁ।",
            "shelter": "ନଇଁ ପଡ଼ନ୍ତୁ, ଢାଙ୍କନ୍ତୁ, ଧରନ୍ତୁ। କମ୍ପ ପରେ କୋଠାରୁ ଦୂରେ ଖୋଲା ସ୍ଥାନକୁ ଯାଆନ୍ତୁ।",
            "tail": "ସରକାରୀ ନିର୍ଦ୍ଦେଶ ପାଳନ କରନ୍ତୁ। — DisasterMind / NDMA",
        },
    }

    def draft_public_alert(
        self,
        event: DisasterEvent,
        languages: tuple[str, ...] = ("en", "hi", "or"),
        place: str | None = None,
    ) -> list[PublicAlert]:
        """Render deterministic public alert copy per language (PRD Step 8).

        Built purely from templates (no LLM) — alert wording must be exact and
        reproducible. Unknown languages fall back to English.
        """
        place = place or (event.meta or {}).get("place") or "your area"
        action = self._ACTION.get(event.kind, "evacuate")
        out: list[PublicAlert] = []
        for lang in languages:
            copy = self._COPY.get(lang) or self._COPY["en"]
            hazard = self._HAZARD.get(event.kind, {}).get(
                lang, self._HAZARD.get(event.kind, {}).get("en", event.kind.value)
            )
            head = copy["head"].format(hazard=hazard, place=place)
            body = f"{copy[action]} {copy['tail']}"
            out.append(PublicAlert(language=lang, headline=head, body=body))
        return out
