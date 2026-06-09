"""CAP 1.2 alerting tests (PRD Step 8 — emergency-broadcast integration).

Pure stdlib, no network: events are constructed directly and the public-alert
copy comes from the deterministic offline ``TemplateClient`` path.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from disastermind.alerting import CAP_NAMESPACE, CapAlert, build_cap_alert
from disastermind.llm import DecisionSupportAdvisor, PublicAlert, TemplateClient
from disastermind.models.domain import DisasterEvent, EventKind
from disastermind.models.geo import LatLon

NS = {"cap": CAP_NAMESPACE}


def _cyclone_event() -> DisasterEvent:
    return DisasterEvent(
        incident_id="CYC-2026-001",
        kind=EventKind.CYCLONE,
        epicentre=LatLon(19.8, 85.8),
        severity=4.0,
        detected_at="2026-06-09T04:30:00Z",
        source="IMD",
        meta={"place": "Puri district"},
    )


def _earthquake_event() -> DisasterEvent:
    return DisasterEvent(
        incident_id="EQ-2026-007",
        kind=EventKind.EARTHQUAKE,
        epicentre=LatLon(27.7, 85.3),
        severity=6.8,
        detected_at="2026-06-09T04:30:00Z",
        source="USGS",
    )


def _three_language_alerts(event: DisasterEvent) -> list[PublicAlert]:
    advisor = DecisionSupportAdvisor(client=TemplateClient())
    alerts = advisor.draft_public_alert(event, languages=("en", "hi", "or"))
    assert len(alerts) == 3
    return alerts


# --------------------------------------------------------------------------- core
def test_cyclone_three_language_cap_is_well_formed():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)

    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Puri district, Odisha",
    )
    assert isinstance(cap, CapAlert)

    xml = cap.to_xml()
    # must re-parse cleanly (well-formed)
    root = ET.fromstring(xml)
    assert root.tag == f"{{{CAP_NAMESPACE}}}alert"

    # envelope fields
    assert root.find("cap:identifier", NS).text
    assert root.find("cap:sender", NS).text == "disastermind@ndma.gov.in"
    assert root.find("cap:status", NS).text == "Actual"
    assert root.find("cap:msgType", NS).text == "Alert"
    assert root.find("cap:scope", NS).text == "Public"
    # <sent> carries the event detection time, normalised with a tz offset
    assert root.find("cap:sent", NS).text == "2026-06-09T04:30:00+00:00"


def test_one_info_block_per_language_in_order():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)

    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Puri district, Odisha",
    )
    root = ET.fromstring(cap.to_xml())
    infos = root.findall("cap:info", NS)
    assert len(infos) == 3

    langs = [i.find("cap:language", NS).text for i in infos]
    assert langs == ["en", "hi", "or"]

    # headline/description carried through from the public-alert copy
    for info, alert in zip(infos, alerts):
        assert info.find("cap:headline", NS).text == alert.headline
        assert info.find("cap:description", NS).text == alert.body


def test_cyclone_severity_urgency_certainty_mapping():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)
    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Puri district, Odisha",
    )
    root = ET.fromstring(cap.to_xml())
    info = root.find("cap:info", NS)
    # cyclone/flood/fire => Immediate / Severe
    assert info.find("cap:urgency", NS).text == "Immediate"
    assert info.find("cap:severity", NS).text == "Severe"
    assert info.find("cap:certainty", NS).text == "Likely"
    # cyclone => Met
    assert info.find("cap:category", NS).text == "Met"
    assert info.find("cap:event", NS).text == "Cyclone"


def test_earthquake_maps_to_extreme_geo():
    event = _earthquake_event()
    alerts = _three_language_alerts(event)
    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Kathmandu valley",
    )
    root = ET.fromstring(cap.to_xml())
    info = root.find("cap:info", NS)
    # earthquake => Immediate / Extreme
    assert info.find("cap:urgency", NS).text == "Immediate"
    assert info.find("cap:severity", NS).text == "Extreme"
    assert info.find("cap:category", NS).text == "Geo"


def test_area_desc_present_in_every_info():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)
    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Puri district, Odisha",
    )
    root = ET.fromstring(cap.to_xml())
    descs = [
        a.find("cap:areaDesc", NS).text
        for a in root.findall("cap:info/cap:area", NS)
    ]
    assert descs == ["Puri district, Odisha"] * 3


# --------------------------------------------------------------------------- area
def test_polygon_emits_cap_area_polygon():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)
    ring = [
        LatLon(19.7, 85.7),
        LatLon(19.9, 85.7),
        LatLon(19.9, 85.9),
        LatLon(19.7, 85.9),
    ]
    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Puri district, Odisha",
        polygon=ring,
    )
    root = ET.fromstring(cap.to_xml())
    polygons = root.findall("cap:info/cap:area/cap:polygon", NS)
    assert len(polygons) == 3  # one per language's <area>
    pts = polygons[0].text.split(" ")
    # auto-closed: first point repeated as the last
    assert pts[0] == "19.7,85.7"
    assert pts[-1] == pts[0]
    assert len(pts) == len(ring) + 1


def test_circle_emits_cap_area_circle():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)
    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Puri district, Odisha",
        circle=(LatLon(19.8, 85.8), 25.0),
    )
    root = ET.fromstring(cap.to_xml())
    circles = root.findall("cap:info/cap:area/cap:circle", NS)
    assert len(circles) == 3
    assert circles[0].text == "19.8,85.8 25.0"


def test_no_polygon_means_no_polygon_element():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)
    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Puri district, Odisha",
    )
    root = ET.fromstring(cap.to_xml())
    assert root.findall("cap:info/cap:area/cap:polygon", NS) == []
    assert root.findall("cap:info/cap:area/cap:circle", NS) == []


# ------------------------------------------------------------------- identifiers
def test_explicit_identifier_is_used():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)
    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="Puri district, Odisha",
        identifier="my-fixed-id-001",
    )
    assert cap.identifier == "my-fixed-id-001"
    root = ET.fromstring(cap.to_xml())
    assert root.find("cap:identifier", NS).text == "my-fixed-id-001"


def test_auto_identifier_is_unique():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)
    a = build_cap_alert(event, alerts, sender="s@x", area_desc="z")
    b = build_cap_alert(event, alerts, sender="s@x", area_desc="z")
    assert a.identifier != b.identifier


def test_xml_special_characters_are_escaped():
    event = _cyclone_event()
    alerts = [PublicAlert(language="en", headline="Storm <surge> & flooding", body="Move now \"fast\"")]
    cap = build_cap_alert(
        event,
        alerts,
        sender="disastermind@ndma.gov.in",
        area_desc="A & B <zone>",
    )
    xml = cap.to_xml()
    # raw angle brackets/ampersands from content must not break parsing
    root = ET.fromstring(xml)
    info = root.find("cap:info", NS)
    assert info.find("cap:headline", NS).text == "Storm <surge> & flooding"
    assert root.find("cap:info/cap:area/cap:areaDesc", NS).text == "A & B <zone>"


def test_missing_detected_at_falls_back_to_now_with_tz():
    event = DisasterEvent(
        incident_id="CYC-X",
        kind=EventKind.FLOOD,
        epicentre=LatLon(19.0, 85.0),
        severity=3.0,
        detected_at="",
    )
    cap = build_cap_alert(
        event,
        [PublicAlert(language="en", headline="h", body="b")],
        sender="s@x",
        area_desc="z",
    )
    root = ET.fromstring(cap.to_xml())
    sent = root.find("cap:sent", NS).text
    # has an explicit UTC offset
    assert sent.endswith("+00:00")


def test_to_xml_without_declaration_still_parses():
    event = _cyclone_event()
    alerts = _three_language_alerts(event)
    cap = build_cap_alert(event, alerts, sender="s@x", area_desc="z")
    body = cap.to_xml(declaration=False)
    assert not body.startswith("<?xml")
    assert ET.fromstring(body).tag.endswith("alert")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
