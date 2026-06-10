"""The :class:`MutualAidCoordinator` — the federation brain (PRD Step 4 & 7).

Responsibilities:

  1. **Compose & emit requests.** Given local uncovered
     :class:`~disastermind.models.domain.ResourceGap` s and a registry of
     adjacent :class:`District` s, pick — per gap — the **nearest** peer that has
     the needed asset type spare, compose an :class:`AidRequest`, and emit it
     **dry-run by default**: the would-be peer call is recorded and a ticket is
     returned. A real ``httpx`` POST happens only when ``live=True`` and a
     transport is wired; ``httpx`` is imported lazily with no test-path network.
  2. **Tag escalation.** A request that crosses a state boundary is tagged
     :class:`EscalationTrigger.CROSS_STATE_RESOURCE` so the commander escalates
     it (Step 7). In-state adjacent-district aid is autonomous (Step 4).
  3. **Answer incoming requests.** Given a peer's :class:`AidRequest`, answer
     with an :class:`AidOffer` sized to the home district's spare capacity, or a
     decline when there is none.

Importing this module is inert and stdlib-only. Nothing here opens a socket
unless you opt into ``live=True`` at the call site.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from ..core.contracts import Message, Priority
from ..models.domain import Asset, AssetType, ResourceGap
from ..models.geo import LatLon
from .model import (
    AidDecision,
    AidOffer,
    AidRequest,
    District,
    offer_to_message,
    request_to_message,
)

log = logging.getLogger("disastermind.federation")

#: A POST transport: ``(url, payload) -> status_code``. Injected only in tests;
#: production lazily uses ``httpx``. Never called in dry-run.
PostTransport = Callable[[str, dict[str, Any]], int]


@dataclass
class AidTicket:
    """Record of a (would-be) mutual-aid request emission.

    ``dispatched`` is ``False`` in dry-run (the default) — the request was
    composed and recorded but **not** sent over the wire. ``status`` is the POST
    status when ``live`` and the call went out, else ``None``.
    """

    request: AidRequest
    message: Message
    dispatched: bool = False
    status: int | None = None
    error: str | None = None

    @property
    def cross_state(self) -> bool:
        return self.request.cross_state


def _default_post_transport(url: str, payload: dict[str, Any]) -> int:  # pragma: no cover - network
    """Lazy ``httpx`` POST with a stdlib :mod:`urllib.request` fallback.

    Only ever reached on the ``live=True`` path; never exercised by tests.
    """
    import json

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "DisasterMind/1.0 federation",
    }
    try:
        import httpx  # type: ignore

        resp = httpx.post(url, content=body, headers=headers, timeout=10.0)
        return int(resp.status_code)
    except ImportError:
        from urllib.request import Request, urlopen

        req = Request(url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=10.0) as resp:  # noqa: S310
            return int(getattr(resp, "status", None) or resp.getcode() or 200)


class MutualAidCoordinator:
    """Compose, emit and answer cross-district mutual-aid (PRD Step 4 & 7).

    Parameters
    ----------
    home:
        The local district this coordinator speaks for (its ``state`` decides
        autonomy; its ``available`` map is the spare pool used to answer
        incoming requests).
    peers:
        The registry of adjacent districts to ask for aid.
    live:
        When ``False`` (default) the coordinator is **dry-run**: requests are
        recorded, never sent. When ``True`` it POSTs each composed request to the
        peer endpoint via ``transport``.
    transport:
        Injected POST transport ``(url, payload) -> status``. Defaults to a lazy
        ``httpx`` transport (only used when ``live=True``).
    """

    def __init__(
        self,
        home: District,
        peers: Iterable[District] | None = None,
        *,
        live: bool = False,
        transport: PostTransport | None = None,
        sender: str | None = None,
    ) -> None:
        self.home = home
        self.peers: list[District] = list(peers or [])
        self.live = bool(live)
        self._transport = transport or _default_post_transport
        self.sender = sender or f"federation.{home.district_id}"
        #: Ledger of every ticket this coordinator has produced (audit/Step 9).
        self.tickets: list[AidTicket] = []

    # ----------------------------------------------------------------- helpers
    def _home_centroid(self) -> LatLon:
        return LatLon(self.home.centroid_lat, self.home.centroid_lon)

    def _peer_distance(self, peer: District) -> float:
        return self._home_centroid().distance_m(
            LatLon(peer.centroid_lat, peer.centroid_lon)
        )

    def nearest_provider(
        self, asset_type: AssetType, *, exclude: Sequence[str] = ()
    ) -> District | None:
        """Return the nearest peer with ``asset_type`` spare, or ``None``.

        Ties (e.g. all peers at the same/zero centroid) break deterministically
        on ``district_id`` so the choice is reproducible across runs.
        """
        excl = set(exclude)
        candidates = [
            p
            for p in self.peers
            if p.district_id not in excl and p.spare(asset_type) > 0
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda p: (self._peer_distance(p), p.district_id),
        )

    def _is_cross_state(self, peer: District) -> bool:
        return peer.state != self.home.state

    # ----------------------------------------------------------- compose & emit
    def compose_request(
        self, gap: ResourceGap, peer: District
    ) -> AidRequest:
        """Compose an :class:`AidRequest` for ``gap`` aimed at ``peer``.

        The requested quantity is the gap shortfall capped at the peer's spare
        capacity (never ask for more than they can give). Cross-state requests
        are flagged so they escalate (Step 7).
        """
        spare = peer.spare(gap.asset_type)
        qty = min(int(gap.shortfall), spare) if spare else int(gap.shortfall)
        cross = self._is_cross_state(peer)
        note = gap.note or (
            "cross-state mutual aid (escalates per PRD Step 7)"
            if cross
            else "in-state adjacent-district mutual aid (PRD Step 4)"
        )
        return AidRequest.new(
            from_district=self.home.district_id,
            to_district=peer.district_id,
            zone_id=gap.zone_id,
            asset_type=gap.asset_type,
            quantity=qty,
            priority=_priority_for_gap(gap),
            cross_state=cross,
            note=note,
        )

    def request_aid(
        self,
        gaps: Iterable[ResourceGap],
        *,
        incident_id: str | None = None,
    ) -> list[AidTicket]:
        """For each gap, find the nearest spare-capacity peer and emit a request.

        Dry-run by default: every emitted request is *recorded* in a ticket and
        on :attr:`tickets`; nothing crosses the network. With ``live=True`` each
        request is POSTed to its peer endpoint. Gaps with no available provider
        are skipped (no ticket).
        """
        out: list[AidTicket] = []
        for gap in gaps:
            if gap.shortfall <= 0:
                continue
            peer = self.nearest_provider(gap.asset_type)
            if peer is None:
                log.info(
                    "no adjacent district has spare %s for zone %s",
                    gap.asset_type.value,
                    gap.zone_id,
                )
                continue
            req = self.compose_request(gap, peer)
            msg = request_to_message(
                req, sender=self.sender, incident_id=incident_id
            )
            ticket = self._emit(req, msg, peer)
            self.tickets.append(ticket)
            out.append(ticket)
        return out

    def _emit(self, req: AidRequest, msg: Message, peer: District) -> AidTicket:
        if not self.live:
            return AidTicket(request=req, message=msg, dispatched=False)
        try:  # pragma: no cover - network path, never hit in tests
            status = self._transport(peer.endpoint, msg.payload)
            return AidTicket(
                request=req, message=msg, dispatched=True, status=status
            )
        except Exception as exc:  # pragma: no cover - network path
            log.warning("mutual-aid POST to %s failed: %s", peer.endpoint, exc)
            return AidTicket(
                request=req, message=msg, dispatched=False, error=str(exc)
            )

    # ----------------------------------------------------- answer incoming aid
    def answer(
        self,
        req: AidRequest,
        *,
        spare: dict[AssetType, int] | None = None,
    ) -> AidOffer:
        """Answer a peer's :class:`AidRequest` from home spare capacity.

        Returns an :class:`AidOffer` sized to ``min(requested, spare)``, or a
        decline (quantity 0) when nothing is spare. Pass ``spare`` to override
        the home district's registry pool (e.g. after live deductions).
        """
        pool = spare if spare is not None else self.home.available
        have = max(0, int(pool.get(req.asset_type, 0)))
        give = min(int(req.quantity), have)
        if give <= 0:
            return AidOffer(
                request_id=req.request_id,
                from_district=self.home.district_id,
                to_district=req.from_district,
                asset_type=req.asset_type,
                decision=AidDecision.DECLINE,
                quantity=0,
                note=f"no spare {req.asset_type.value} available",
            )
        return AidOffer(
            request_id=req.request_id,
            from_district=self.home.district_id,
            to_district=req.from_district,
            asset_type=req.asset_type,
            decision=AidDecision.OFFER,
            quantity=give,
            note=(
                f"offering {give} of {req.quantity} requested "
                f"{req.asset_type.value}"
            ),
        )

    def answer_message(
        self, req: AidRequest, *, incident_id: str | None = None
    ) -> Message:
        """Convenience: :meth:`answer` then wrap the offer in a bus message."""
        offer = self.answer(req)
        return offer_to_message(
            offer, sender=self.sender, incident_id=incident_id
        )


def _priority_for_gap(gap: ResourceGap) -> Priority:
    """Map a gap's shortfall onto a message priority (bigger gap => more urgent)."""
    if gap.shortfall >= 5:
        return Priority.CRITICAL
    if gap.shortfall >= 2:
        return Priority.HIGH
    return Priority.MEDIUM


def spare_from_assets(assets: Iterable[Asset]) -> dict[AssetType, int]:
    """Build a spare-capacity map from concrete :class:`Asset` s (available only).

    A small helper so a caller can derive a :class:`District.available` map from
    its real asset inventory rather than hand-maintaining counts.
    """
    pool: dict[AssetType, int] = {}
    for a in assets:
        if a.available:
            pool[a.type] = pool.get(a.type, 0) + 1
    return pool
