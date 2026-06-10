"""Social-media NLP agent tests (PRD Step 1 Module C trigger + PRD Step 2).

Stdlib-only. Covers:

  * the keyword scorer ranks disaster/collapse posts above ambient noise
    (English + Hindi/Devanagari);
  * a tight geo-temporal cluster emits exactly one RAW_FEED ALERT carrying a
    Module-C DisasterEvent above threshold, and stays silent below it;
  * Tier 3 invariants (Tier.EDGE, no decision authority) and the uniform
    ``build.build_agents`` factory contract.
"""
from __future__ import annotations

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from disastermind.tier3.social import SocialNLPAgent, score_post
from disastermind.tier3.social.agent import normalise, score_text
from disastermind.tier3.social.build import build_agents


def _agent(**kw) -> SocialNLPAgent:
    return SocialNLPAgent(
        bus=InMemoryBus(), logger=DecisionLogger.null(), settings=Settings(), **kw
    )


# --------------------------------------------------------------------- scoring
def test_normalise_keeps_devanagari_strips_urls_and_mentions():
    norm = normalise("HELP!! @ndrf बचाओ https://t.co/x #collapse")
    assert "बचाओ" in norm
    assert "collapse" in norm
    assert "help" in norm
    assert "http" not in norm
    assert "@ndrf" not in norm


def test_keyword_scorer_ranks_disaster_posts_above_noise():
    """Disaster/collapse posts must outscore off-topic chatter (PRD Step 2)."""
    disaster = "Building collapsed! people trapped under rubble, need rescue"
    hindi = "इमारत गिर गई, लोग मलबे में फंसे हैं बचाओ"
    noise = "loving this sunny weather, perfect for a walk in the park"
    quiet = "just had a great cup of coffee this morning"

    d_score, d_matched = score_text(disaster)
    h_score, h_matched = score_text(hindi)
    n_score, _ = score_text(noise)
    q_score, _ = score_text(quiet)

    assert d_score > n_score
    assert d_score > q_score
    assert h_score > n_score  # multilingual signal also ranks above noise
    assert n_score == 0.0 and q_score == 0.0
    assert "trapped" in d_matched
    assert any("बचाओ" == m or "फंस" == m or "गिर" == m for m in h_matched)


def test_score_post_convenience_matches_score_text():
    assert score_post("fire and smoke everywhere") == score_text(
        "fire and smoke everywhere"
    )


def test_score_post_confidence_in_unit_interval():
    a = _agent()
    sp = a.score_post(
        type(a).__mro__  # placeholder to ensure import; replaced below
        and __import__(
            "disastermind.tier3.social.agent", fromlist=["SocialPost"]
        ).SocialPost(
            post_id="p1",
            text="building collapsed, trapped, save us",
            lat=28.56,
            lon=77.24,
            created_at="2026-06-08T07:10:00+00:00",
        )
    )
    assert 0.0 <= sp.confidence <= 1.0
    assert sp.confidence > 0.0
    assert sp.raw_score > 0.0


# -------------------------------------------------------------- cluster / alert
def test_tight_cluster_emits_single_alert_above_threshold():
    """A tight geo-temporal collapse cluster fires exactly one RAW_FEED ALERT."""
    a = _agent()
    msgs = a.tick()  # default sample = 3 corroborating posts + 2 noise/lone

    alerts = [m for m in msgs if m.type is MessageType.ALERT]
    assert len(alerts) == 1
    msg = alerts[0]
    assert msg.topic == Topic.RAW_FEED
    assert msg.module is Module.FIRE_COLLAPSE
    assert msg.priority in (Priority.HIGH, Priority.CRITICAL)

    event = msg.payload["event"]
    assert event is not None
    assert event["kind"] == "structural_collapse"
    assert msg.incident_id == event["incident_id"]
    # centroid sits with the clustered posts (Lajpat Nagar ~28.567, 77.244),
    # NOT at the off-topic/lone post locations.
    assert 28.56 <= event["epicentre"]["lat"] <= 28.57
    assert 77.24 <= event["epicentre"]["lon"] <= 77.25
    assert event["meta"]["cluster_size"] >= a.min_cluster_size
    assert 0.0 < event["meta"]["confidence"] <= 1.0


def test_below_threshold_stays_silent():
    """Fewer corroborating posts than the cluster quorum => no alert."""
    a = _agent(min_cluster_size=5)  # raise the bar above the 3-post fixture
    assert a.tick() == []

    # Also: a lone distress post in its own bucket must not trip an alert.
    a2 = _agent()
    lone = [
        {
            "post_id": "solo",
            "text": "building collapsed people trapped save us!",
            "lat": 12.9716,
            "lon": 77.5946,
            "created_at": "2026-06-08T07:10:00+00:00",
        }
    ]
    assert a2.detect(lone) == []


def test_noise_only_batch_produces_no_cluster():
    a = _agent()
    noise = [
        {
            "post_id": "n1",
            "text": "great weather today!",
            "lat": 28.5670,
            "lon": 77.2430,
            "created_at": "2026-06-08T07:10:00+00:00",
        },
        {
            "post_id": "n2",
            "text": "loving my new phone",
            "lat": 28.5671,
            "lon": 77.2431,
            "created_at": "2026-06-08T07:10:30+00:00",
        },
        {
            "post_id": "n3",
            "text": "coffee time",
            "lat": 28.5672,
            "lon": 77.2432,
            "created_at": "2026-06-08T07:11:00+00:00",
        },
    ]
    assert a.detect(noise) == []


def test_time_window_excludes_stale_posts():
    """Posts outside the time window do not corroborate the cluster (PRD Step 2)."""
    a = _agent()
    base = [
        {
            "post_id": "t1",
            "text": "building collapsed, people trapped, rescue!",
            "lat": 28.5670,
            "lon": 77.2430,
            "created_at": "2026-06-08T07:10:00+00:00",
        },
        {
            "post_id": "t2",
            "text": "इमारत गिर गई, मलबे में फंसे बचाओ",
            "lat": 28.5675,
            "lon": 77.2438,
            "created_at": "2026-06-08T07:11:00+00:00",
        },
        # hours earlier — should be excluded by a 30-min window.
        {
            "post_id": "t3-stale",
            "text": "whole building came down, debris, trapped",
            "lat": 28.5681,
            "lon": 77.2442,
            "created_at": "2026-06-08T02:00:00+00:00",
        },
    ]
    # With the stale post excluded, only 2 in-window posts -> below quorum (3).
    assert a.detect(base, window_seconds=1800, now="2026-06-08T07:12:00+00:00") == []
    # Widen the window to include all three -> a cluster forms.
    clusters = a.detect(base, window_seconds=86_400, now="2026-06-08T07:12:00+00:00")
    assert len(clusters) == 1
    assert clusters[0]["size"] == 3


# ------------------------------------------------------------------ invariants
def test_tier3_no_decision_authority():
    a = _agent()
    assert a.tier is Tier.EDGE
    assert a.decision_authority is False
    assert a.handle.__doc__  # pure producer
    assert a.handle(object()) == []  # type: ignore[arg-type]


def test_build_factory_returns_social_agent():
    bus = InMemoryBus()
    agents = build_agents(bus, DecisionLogger.null(), Settings())
    assert len(agents) == 1
    assert isinstance(agents[0], SocialNLPAgent)
    assert agents[0].tier is Tier.EDGE
    assert agents[0].decision_authority is False


def test_emitted_alert_event_payload_matches_prediction_contract():
    """The RAW_FEED payload must carry an ``event`` dict the Tier 2 prediction
    agent can read (payload.get('event') -> dict with string 'kind')."""
    a = _agent()
    a.emit_calls = []  # type: ignore[attr-defined]
    msgs = a.tick()
    assert msgs
    payload = msgs[0].payload
    ev = payload.get("event")
    assert isinstance(ev, dict)
    assert isinstance(ev["kind"], str)
    assert "epicentre" in ev and "lat" in ev["epicentre"]
