"""Cross-district mutual-aid federation tests (PRD Step 4 & Step 7).

Pure stdlib, no network. The coordinator is exercised entirely in its default
dry-run mode (requests are recorded, never sent) plus an injected POST transport
stub to prove the live path stays a no-op until opted in. No real socket is ever
opened: there is no ``transport`` that talks to the network anywhere here.
"""
from __future__ import annotations

import pytest

from disastermind.core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Priority,
)
from disastermind.federation import (
    AidDecision,
    AidOffer,
    AidRequest,
    District,
    MutualAidCoordinator,
    offer_from_message,
    offer_to_message,
    request_from_message,
    request_to_message,
    spare_from_assets,
)
from disastermind.models.domain import Asset, AssetType, ResourceGap
from disastermind.models.geo import LatLon


# --------------------------------------------------------------------------- fixtures
def _home(state: str = "Odisha") -> District:
    """Local district at the origin with no spare boats (it needs aid)."""
    return District(
        district_id="PURI",
        name="Puri",
        state=state,
        endpoint="https://puri.example/aid",
        centroid_lat=19.80,
        centroid_lon=85.83,
        available={AssetType.MEDICAL_UNIT: 2},
    )


def _near_in_state() -> District:
    """Adjacent in-state district ~60 km away with spare boats."""
    return District(
        district_id="KHORDHA",
        name="Khordha",
        state="Odisha",
        endpoint="https://khordha.example/aid",
        centroid_lat=20.18,
        centroid_lon=85.62,
        available={AssetType.BOAT: 4, AssetType.NDRF_TEAM: 1},
    )


def _far_in_state() -> District:
    """A further in-state district, also with spare boats (should lose to nearer)."""
    return District(
        district_id="CUTTACK",
        name="Cuttack",
        state="Odisha",
        endpoint="https://cuttack.example/aid",
        centroid_lat=20.46,
        centroid_lon=85.88,
        available={AssetType.BOAT: 10},
    )


def _cross_state() -> District:
    """Adjacent district across a state line with spare helicopters."""
    return District(
        district_id="SRIKAKULAM",
        name="Srikakulam",
        state="Andhra Pradesh",
        endpoint="https://srikakulam.example/aid",
        centroid_lat=18.30,
        centroid_lon=83.90,
        available={AssetType.HELICOPTER: 3},
    )


# ----------------------------------------------------- (1) compose request to nearest
def test_gap_with_no_local_cover_requests_nearest_adjacent_with_spare():
    coord = MutualAidCoordinator(
        home=_home(),
        peers=[_far_in_state(), _near_in_state()],  # order shouldn't matter
    )
    gap = ResourceGap(zone_id="Z-coast-7", asset_type=AssetType.BOAT, shortfall=3)

    tickets = coord.request_aid([gap])

    assert len(tickets) == 1
    t = tickets[0]
    # nearest spare-capacity provider wins (Khordha is closer than Cuttack)
    assert t.request.to_district == "KHORDHA"
    assert t.request.asset_type is AssetType.BOAT
    # quantity capped at peer spare but here shortfall (3) < spare (4)
    assert t.request.quantity == 3
    # DRY-RUN by default: composed + recorded but NOT sent
    assert t.dispatched is False
    assert t.status is None
    assert coord.tickets == tickets  # ledger updated


def test_quantity_capped_at_peer_spare_capacity():
    coord = MutualAidCoordinator(home=_home(), peers=[_near_in_state()])
    gap = ResourceGap(zone_id="Z-9", asset_type=AssetType.BOAT, shortfall=9)
    [t] = coord.request_aid([gap])
    # Khordha only has 4 boats spare -> never ask for more than 4
    assert t.request.quantity == 4


def test_no_provider_for_asset_yields_no_ticket():
    coord = MutualAidCoordinator(home=_home(), peers=[_near_in_state()])
    # nobody has a fire engine spare
    gap = ResourceGap(zone_id="Z-1", asset_type=AssetType.FIRE_ENGINE, shortfall=2)
    assert coord.request_aid([gap]) == []
    assert coord.tickets == []


def test_zero_shortfall_gap_is_skipped():
    coord = MutualAidCoordinator(home=_home(), peers=[_near_in_state()])
    gap = ResourceGap(zone_id="Z-1", asset_type=AssetType.BOAT, shortfall=0)
    assert coord.request_aid([gap]) == []


# ------------------------------------------------- (3) cross-state escalation tagging
def test_cross_state_request_triggers_cross_state_resource_escalation():
    coord = MutualAidCoordinator(home=_home(), peers=[_cross_state()])
    gap = ResourceGap(zone_id="Z-air", asset_type=AssetType.HELICOPTER, shortfall=2)

    [t] = coord.request_aid([gap])

    assert t.request.to_district == "SRIKAKULAM"
    assert t.request.cross_state is True
    assert t.cross_state is True
    # the request's escalation trigger and the bus message both carry it (Step 7)
    assert t.request.escalation_trigger is EscalationTrigger.CROSS_STATE_RESOURCE
    assert (
        t.message.escalation_trigger is EscalationTrigger.CROSS_STATE_RESOURCE
    )
    assert t.message.type is MessageType.ESCALATION


def test_in_state_request_is_autonomous_no_escalation():
    coord = MutualAidCoordinator(home=_home(), peers=[_near_in_state()])
    gap = ResourceGap(zone_id="Z-coast", asset_type=AssetType.BOAT, shortfall=2)
    [t] = coord.request_aid([gap])
    assert t.request.cross_state is False
    assert t.request.escalation_trigger is None
    assert t.message.escalation_trigger is None
    # in-state adjacent aid rides as an autonomous QUERY (Step 4), not an escalation
    assert t.message.type is MessageType.QUERY


def test_nearest_preferred_even_when_cross_state_is_closer_but_lacks_asset():
    # cross-state peer is geographically nearest but has no boats;
    # in-state Khordha (has boats) must still be chosen for a boat gap.
    coord = MutualAidCoordinator(
        home=_home(), peers=[_cross_state(), _near_in_state()]
    )
    gap = ResourceGap(zone_id="Z-coast", asset_type=AssetType.BOAT, shortfall=1)
    [t] = coord.request_aid([gap])
    assert t.request.to_district == "KHORDHA"
    assert t.request.cross_state is False


# ----------------------------------------- (2) answer incoming request from spare cap
def test_incoming_request_answered_with_offer_sized_to_spare():
    home = District(
        district_id="KHORDHA",
        name="Khordha",
        state="Odisha",
        endpoint="https://khordha.example/aid",
        available={AssetType.BOAT: 4},
    )
    coord = MutualAidCoordinator(home=home, peers=[])
    incoming = AidRequest.new(
        from_district="PURI",
        to_district="KHORDHA",
        zone_id="Z-coast-7",
        asset_type=AssetType.BOAT,
        quantity=6,  # asks for more than the 4 spare
        priority=Priority.HIGH,
    )

    offer = coord.answer(incoming)

    assert offer.decision is AidDecision.OFFER
    assert offer.is_offer is True
    # offer sized to spare: min(requested 6, spare 4) == 4
    assert offer.quantity == 4
    assert offer.from_district == "KHORDHA"
    assert offer.to_district == "PURI"
    assert offer.request_id == incoming.request_id


def test_incoming_request_declined_when_no_spare():
    home = District(
        district_id="KHORDHA",
        name="Khordha",
        state="Odisha",
        endpoint="x",
        available={AssetType.BOAT: 0},
    )
    coord = MutualAidCoordinator(home=home, peers=[])
    incoming = AidRequest.new(
        from_district="PURI",
        to_district="KHORDHA",
        zone_id="Z-1",
        asset_type=AssetType.BOAT,
        quantity=3,
    )
    offer = coord.answer(incoming)
    assert offer.decision is AidDecision.DECLINE
    assert offer.is_offer is False
    assert offer.quantity == 0


def test_answer_can_override_spare_pool():
    coord = MutualAidCoordinator(home=_home(), peers=[])
    incoming = AidRequest.new(
        from_district="PURI",
        to_district=_home().district_id,
        zone_id="Z-1",
        asset_type=AssetType.BOAT,
        quantity=5,
    )
    # home registry has no boats, but live override says 2 are now spare
    offer = coord.answer(incoming, spare={AssetType.BOAT: 2})
    assert offer.decision is AidDecision.OFFER
    assert offer.quantity == 2


# ------------------------------------------------- (4) Message round-trip both ways
def test_request_round_trips_through_message_payload():
    req = AidRequest.new(
        from_district="PURI",
        to_district="SRIKAKULAM",
        zone_id="Z-air",
        asset_type=AssetType.HELICOPTER,
        quantity=2,
        priority=Priority.CRITICAL,
        cross_state=True,
        note="urgent air rescue",
    )
    msg = request_to_message(req, sender="federation.PURI", incident_id="INC-1")
    assert isinstance(msg, Message)

    back = request_from_message(msg)
    assert back == req
    # and survives a full dict serialisation of the envelope (audit/bus)
    assert msg.to_dict()["escalation_trigger"] == (
        EscalationTrigger.CROSS_STATE_RESOURCE.value
    )
    assert msg.incident_id == "INC-1"


def test_offer_round_trips_through_message_payload():
    offer = AidOffer(
        request_id="AID-REQ-abc123",
        from_district="KHORDHA",
        to_district="PURI",
        asset_type=AssetType.BOAT,
        decision=AidDecision.OFFER,
        quantity=4,
        note="offering 4 of 6",
    )
    msg = offer_to_message(offer, incident_id="INC-9")
    assert msg.type is MessageType.ACK
    assert msg.recipient == "PURI"

    back = offer_from_message(msg)
    assert back == offer


def test_decline_offer_round_trips():
    offer = AidOffer(
        request_id="AID-REQ-xyz",
        from_district="KHORDHA",
        to_district="PURI",
        asset_type=AssetType.BOAT,
        decision=AidDecision.DECLINE,
        quantity=0,
    )
    back = offer_from_message(offer_to_message(offer))
    assert back == offer
    assert back.is_offer is False


def test_wrong_kind_payload_rejected():
    bad = Message(
        sender="x",
        recipient="y",
        type=MessageType.QUERY,
        priority=Priority.LOW,
        payload={"kind": "something_else"},
    )
    with pytest.raises(ValueError):
        request_from_message(bad)
    with pytest.raises(ValueError):
        offer_from_message(bad)


# ---------------------------------------------------------- live path stays opt-in
def test_live_mode_uses_injected_transport_dry_run_does_not():
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, payload: dict) -> int:
        calls.append((url, payload))
        return 202

    # dry-run (default): transport must NOT be called
    dry = MutualAidCoordinator(
        home=_home(), peers=[_near_in_state()], transport=fake_post
    )
    [dt] = dry.request_aid(
        [ResourceGap(zone_id="Z", asset_type=AssetType.BOAT, shortfall=2)]
    )
    assert dt.dispatched is False
    assert calls == []

    # live: transport IS called, ticket records dispatch + status
    live = MutualAidCoordinator(
        home=_home(),
        peers=[_near_in_state()],
        live=True,
        transport=fake_post,
    )
    [lt] = live.request_aid(
        [ResourceGap(zone_id="Z", asset_type=AssetType.BOAT, shortfall=2)]
    )
    assert lt.dispatched is True
    assert lt.status == 202
    assert len(calls) == 1
    assert calls[0][0] == _near_in_state().endpoint
    assert calls[0][1]["kind"] == "mutual_aid_request"


# ------------------------------------------------------- helper: spare_from_assets
def test_spare_from_assets_counts_only_available():
    here = LatLon(20.0, 85.0)
    assets = [
        Asset(asset_id="b1", type=AssetType.BOAT, location=here, available=True),
        Asset(asset_id="b2", type=AssetType.BOAT, location=here, available=True),
        Asset(asset_id="b3", type=AssetType.BOAT, location=here, available=False),
        Asset(
            asset_id="h1",
            type=AssetType.HELICOPTER,
            location=here,
            available=True,
        ),
    ]
    pool = spare_from_assets(assets)
    assert pool == {AssetType.BOAT: 2, AssetType.HELICOPTER: 1}
