"""Common Alerting Protocol (CAP 1.2) emergency-broadcast output (PRD Step 8).

This module turns a :class:`~disastermind.models.domain.DisasterEvent` plus the
multi-language :class:`~disastermind.llm.PublicAlert` copy produced by
:meth:`DecisionSupportAdvisor.draft_public_alert` into a standards-compliant
CAP 1.2 ``<alert>`` document.

The document is assembled with the standard-library ``xml.etree.ElementTree``
serialiser only — *no* ``lxml`` and *no* network. One ``<info>`` block is
emitted per public-alert language, with category / urgency / severity /
certainty mapped from the hazard kind, and a single ``<area>`` carrying the
human-readable description plus an optional ``<polygon>`` and/or ``<circle>``.

The output re-parses cleanly with :func:`xml.etree.ElementTree.fromstring`
(it is well-formed). The module is inert/opt-in: importing it has no side
effects and it is never wired into the live bus, so the existing suite stays
green.

Reference: OASIS CAP v1.2 (urn:oasis:names:tc:emergency:cap:1.2).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence
from xml.etree import ElementTree as ET

from ..llm import PublicAlert
from ..models.domain import DisasterEvent, EventKind
from ..models.geo import LatLon

CAP_NAMESPACE = "urn:oasis:names:tc:emergency:cap:1.2"

__all__ = ["CAP_NAMESPACE", "CapAlert", "build_cap_alert"]


# --------------------------------------------------------------------------- maps
# CAP 1.2 <category> codes: Geo, Met, Safety, Security, Rescue, Fire, Health,
# Env, Transport, Infra, CBRNE, Other.
_CATEGORY: dict[EventKind, str] = {
    EventKind.CYCLONE: "Met",
    EventKind.FLOOD: "Met",
    EventKind.EARTHQUAKE: "Geo",
    EventKind.URBAN_FIRE: "Fire",
    EventKind.STRUCTURAL_COLLAPSE: "Safety",
}

# CAP 1.2 <urgency>: Immediate, Expected, Future, Past, Unknown.
# CAP 1.2 <severity>: Extreme, Severe, Moderate, Minor, Unknown.
# CAP 1.2 <certainty>: Observed, Likely, Possible, Unlikely, Unknown.
# Hazard -> (urgency, severity, certainty).
_HAZARD_LEVELS: dict[EventKind, tuple[str, str, str]] = {
    EventKind.CYCLONE: ("Immediate", "Severe", "Likely"),
    EventKind.FLOOD: ("Immediate", "Severe", "Likely"),
    EventKind.URBAN_FIRE: ("Immediate", "Severe", "Observed"),
    EventKind.EARTHQUAKE: ("Immediate", "Extreme", "Observed"),
    EventKind.STRUCTURAL_COLLAPSE: ("Immediate", "Extreme", "Observed"),
}

_DEFAULT_LEVELS = ("Immediate", "Severe", "Likely")
_DEFAULT_CATEGORY = "Other"

# Human-readable hazard label for the CAP <event> element.
_EVENT_LABEL: dict[EventKind, str] = {
    EventKind.CYCLONE: "Cyclone",
    EventKind.FLOOD: "Flood",
    EventKind.EARTHQUAKE: "Earthquake",
    EventKind.URBAN_FIRE: "Urban Fire",
    EventKind.STRUCTURAL_COLLAPSE: "Structural Collapse",
}


def _category_for(kind: EventKind) -> str:
    return _CATEGORY.get(kind, _DEFAULT_CATEGORY)


def _levels_for(kind: EventKind) -> tuple[str, str, str]:
    return _HAZARD_LEVELS.get(kind, _DEFAULT_LEVELS)


def _event_label(event: DisasterEvent) -> str:
    return _EVENT_LABEL.get(event.kind, str(getattr(event.kind, "value", event.kind)))


def _iso_now() -> str:
    """CAP-style timestamp with explicit timezone offset (RFC 3339)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _normalise_sent(event: DisasterEvent) -> str:
    """Prefer the event detection time; fall back to now. Always has an offset."""
    sent = (event.detected_at or "").strip()
    if not sent:
        return _iso_now()
    # CAP requires a timezone designator; append UTC if the ISO string lacks one.
    has_tz = sent.endswith("Z") or ("+" in sent[10:]) or ("-" in sent[10:])
    if sent.endswith("Z"):
        sent = sent[:-1] + "+00:00"
    elif not has_tz:
        sent = sent + "+00:00"
    return sent


def _polygon_string(points: Sequence[LatLon]) -> str:
    """CAP polygon: space-separated 'lat,lon' pairs, first point repeated last."""
    coords = list(points)
    if not coords:
        return ""
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    return " ".join(f"{p.lat},{p.lon}" for p in coords)


def _circle_string(centre: LatLon, radius_km: float) -> str:
    """CAP circle: 'lat,lon radius' where radius is in kilometres."""
    return f"{centre.lat},{centre.lon} {float(radius_km)}"


# --------------------------------------------------------------------------- model
@dataclass
class CapAlert:
    """A built CAP 1.2 alert wrapping its ElementTree ``<alert>`` root.

    Use :meth:`to_xml` for the serialised, declaration-prefixed document. The
    raw :attr:`element` is exposed for callers that want to inspect or re-parse
    the tree directly.
    """

    element: ET.Element
    identifier: str
    sender: str
    sent: str
    languages: tuple[str, ...] = field(default_factory=tuple)

    def to_xml(self, *, declaration: bool = True) -> str:
        """Serialise to a well-formed CAP 1.2 XML string (stdlib only)."""
        body = ET.tostring(self.element, encoding="unicode")
        if declaration:
            return '<?xml version="1.0" encoding="UTF-8"?>\n' + body
        return body

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.to_xml()


# --------------------------------------------------------------------------- build
def build_cap_alert(
    event: DisasterEvent,
    public_alerts: Iterable[PublicAlert],
    *,
    sender: str,
    area_desc: str,
    polygon: Optional[Sequence[LatLon]] = None,
    circle: Optional[tuple[LatLon, float]] = None,
    identifier: Optional[str] = None,
    status: str = "Actual",
    msg_type: str = "Alert",
    scope: str = "Public",
) -> CapAlert:
    """Build a CAP 1.2 ``<alert>`` document from an event + public-alert copy.

    Parameters
    ----------
    event:
        The source :class:`DisasterEvent`; its ``kind`` drives the CAP
        ``<category>`` and the urgency/severity/certainty mapping, and its
        ``detected_at`` becomes ``<sent>``.
    public_alerts:
        The multi-language :class:`PublicAlert` copy (typically from
        :meth:`DecisionSupportAdvisor.draft_public_alert`). One ``<info>`` block
        is emitted per alert, in order.
    sender:
        The CAP ``<sender>`` identity (e.g. ``"disastermind@ndma.gov.in"``).
    area_desc:
        Human-readable ``<areaDesc>`` text for the single ``<area>`` block.
    polygon:
        Optional ordered :class:`LatLon` ring -> a CAP ``<polygon>``. The ring
        is auto-closed (first point repeated) if needed.
    circle:
        Optional ``(centre, radius_km)`` -> a CAP ``<circle>``.
    identifier:
        Optional explicit ``<identifier>``; a deterministic-free UUID is minted
        when omitted.
    status / msg_type / scope:
        CAP envelope fields, defaulting to ``Actual`` / ``Alert`` / ``Public``.

    Returns
    -------
    CapAlert
        Wrapper around the built tree; call :meth:`CapAlert.to_xml`.
    """
    alerts = list(public_alerts)
    identifier = identifier or f"dm-cap-{uuid.uuid4().hex}"
    sent = _normalise_sent(event)

    category = _category_for(event.kind)
    urgency, severity, certainty = _levels_for(event.kind)
    event_label = _event_label(event)

    root = ET.Element("alert", {"xmlns": CAP_NAMESPACE})
    _text(root, "identifier", identifier)
    _text(root, "sender", sender)
    _text(root, "sent", sent)
    _text(root, "status", status)
    _text(root, "msgType", msg_type)
    _text(root, "scope", scope)

    languages: list[str] = []
    for alert in alerts:
        lang = alert.language or "en"
        languages.append(lang)
        info = ET.SubElement(root, "info")
        _text(info, "language", lang)
        _text(info, "category", category)
        _text(info, "event", event_label)
        _text(info, "urgency", urgency)
        _text(info, "severity", severity)
        _text(info, "certainty", certainty)
        _text(info, "headline", alert.headline)
        _text(info, "description", alert.body)
        # The PublicAlert body carries the call-to-action; surface it as the
        # CAP <instruction> too so downstream broadcasters get an action field.
        _text(info, "instruction", alert.body)

        area = ET.SubElement(info, "area")
        _text(area, "areaDesc", area_desc)
        if polygon:
            poly = _polygon_string(polygon)
            if poly:
                _text(area, "polygon", poly)
        if circle:
            centre, radius_km = circle
            _text(area, "circle", _circle_string(centre, radius_km))

    return CapAlert(
        element=root,
        identifier=identifier,
        sender=sender,
        sent=sent,
        languages=tuple(languages),
    )


def _text(parent: ET.Element, tag: str, value: object) -> ET.Element:
    """Append a child element whose text is the stringified ``value``."""
    child = ET.SubElement(parent, tag)
    child.text = "" if value is None else str(value)
    return child
