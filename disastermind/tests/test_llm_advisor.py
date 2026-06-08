"""Group B decision-support advisor tests (PRD Step 7/8).

Deterministic + offline (TemplateClient echoes the built body), so every path is
reproducible with no network. Verifies the advisor is advisory-only (emits
nothing), produces a situation brief from the bus, recommends nearest-asset
reallocations, and renders multi-language public alert copy.
"""
from __future__ import annotations

from disastermind.llm import (
    DecisionSupportAdvisor,
    PublicAlert,
    ReallocationAdvice,
    TemplateClient,
)
from disastermind.models.domain import DisasterEvent, EventKind
from disastermind.models.geo import LatLon
from disastermind.scenarios import simulate_earthquake


def _advisor() -> DecisionSupportAdvisor:
    # force the deterministic offline client regardless of any env key
    return DecisionSupportAdvisor(client=TemplateClient())


# ----------------------------------------------------------------- situation brief
def test_situation_brief_summarises_live_incident():
    loop = simulate_earthquake()
    brief = _advisor().situation_brief(loop.bus)
    assert "DISASTERMIND SITUATION BRIEF" in brief
    assert "active modules" in brief
    # the earthquake scenario reaches dispatch, so the assessment reflects it
    assert "autonomous response active" in brief
    assert "ASSESSMENT" in brief


def test_situation_brief_filters_by_incident_id():
    loop = simulate_earthquake()
    # an unknown incident id yields a well-formed but empty-observation brief
    brief = _advisor().situation_brief(loop.bus, incident_id="does-not-exist")
    assert "messages observed: 0" in brief
    assert "no autonomous dispatch yet" in brief


def test_advisor_emits_nothing_on_the_bus():
    """Advisory only: calling the advisor must not publish to the bus (Step 7)."""
    loop = simulate_earthquake()
    before = len(loop.bus.history)
    adv = _advisor()
    adv.situation_brief(loop.bus)
    adv.recommend_reallocation([], [])
    adv.draft_public_alert(
        DisasterEvent("i", EventKind.CYCLONE, LatLon(20.0, 85.0), 3.0, "2026-06-08T00:00:00+00:00")
    )
    assert len(loop.bus.history) == before


# ------------------------------------------------------------- reallocation advice
def test_recommend_reallocation_picks_nearest_spare_of_right_type():
    gaps = [{"zone_id": "Z1", "asset_type": "boat", "shortfall": 1, "location": {"lat": 20.30, "lon": 85.80}}]
    spare = [
        {"asset_id": "BOAT-far", "type": "boat", "location": {"lat": 21.50, "lon": 86.90}, "available": True},
        {"asset_id": "BOAT-near", "type": "boat", "location": {"lat": 20.31, "lon": 85.81}, "available": True},
        {"asset_id": "HELI-1", "type": "helicopter", "location": {"lat": 20.30, "lon": 85.80}, "available": True},
    ]
    advice = _advisor().recommend_reallocation(gaps, spare)
    assert isinstance(advice, ReallocationAdvice)
    assert len(advice.moves) == 1
    move = advice.moves[0]
    assert move.asset_id == "BOAT-near"      # nearest boat, not the helicopter
    assert move.to_zone == "Z1"
    assert move.distance_km >= 0
    assert not advice.uncovered


def test_recommend_reallocation_reports_uncovered_when_no_spare():
    gaps = [{"zone_id": "Z9", "asset_type": "usar_team", "shortfall": 2}]
    advice = _advisor().recommend_reallocation(gaps, spare_assets=[])
    assert advice.moves == []
    assert advice.uncovered == ["Z9"]


def test_reallocation_does_not_reuse_an_asset_across_gaps():
    gaps = [
        {"zone_id": "A", "asset_type": "boat", "location": {"lat": 20.0, "lon": 85.0}},
        {"zone_id": "B", "asset_type": "boat", "location": {"lat": 20.0, "lon": 85.0}},
    ]
    spare = [{"asset_id": "ONLY-BOAT", "type": "boat", "location": {"lat": 20.0, "lon": 85.0}}]
    advice = _advisor().recommend_reallocation(gaps, spare)
    assert len(advice.moves) == 1 and advice.uncovered == ["B"]


# --------------------------------------------------------------- public alerting
def test_draft_public_alert_multilingual_and_action_by_hazard():
    ev = DisasterEvent("i", EventKind.CYCLONE, LatLon(20.0, 85.0), 4.0, "2026-06-08T00:00:00+00:00",
                       meta={"place": "Puri district"})
    alerts = _advisor().draft_public_alert(ev, languages=("en", "hi", "or"))
    assert [a.language for a in alerts] == ["en", "hi", "or"]
    assert all(isinstance(a, PublicAlert) for a in alerts)
    en = alerts[0]
    assert "cyclone" in en.headline and "Puri district" in en.headline
    assert "Evacuate" in en.body                       # cyclone -> evacuate
    # non-English copy is genuinely localised (not the English string)
    assert "चक्रवात" in alerts[1].headline
    assert "ବାତ୍ୟା" in alerts[2].headline


def test_draft_public_alert_earthquake_is_shelter_in_place():
    ev = DisasterEvent("i", EventKind.EARTHQUAKE, LatLon(20.0, 85.0), 6.0, "2026-06-08T00:00:00+00:00")
    en = _advisor().draft_public_alert(ev, languages=("en",))[0]
    assert "Drop, cover, hold" in en.body              # quake -> shelter, not evacuate
    assert "your area" in en.headline                  # default place


def test_unknown_language_falls_back_to_english():
    ev = DisasterEvent("i", EventKind.URBAN_FIRE, LatLon(20.0, 85.0), 2.0, "2026-06-08T00:00:00+00:00")
    alert = _advisor().draft_public_alert(ev, languages=("zz",))[0]
    assert alert.language == "zz"
    assert "fire" in alert.headline                    # English fallback copy
