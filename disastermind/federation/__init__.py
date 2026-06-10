"""Cross-district mutual-aid federation (PRD Step 4 & Step 7).

When a district's own resourcing leaves a :class:`~disastermind.models.domain.ResourceGap`
uncovered, DisasterMind does what a human incident commander does next: it asks
the **neighbours**. This package implements that federation layer.

  * In-state, adjacent-district aid is **autonomous** (PRD Step 4 "request mutual
    aid from adjacent districts"): the coordinator composes the request and a
    Tier-2 commander may act on it without a human.
  * A request that has to cross a **state boundary** is tagged with
    :class:`~disastermind.core.contracts.EscalationTrigger.CROSS_STATE_RESOURCE`
    so the Tier-1 commander escalates it for human approval (PRD Step 7).

Everything here is **offline / dry-run by default**. Calling
:meth:`MutualAidCoordinator.request_aid` *records* the would-be peer request and
returns a ticket; it performs **no network I/O**. A real cross-district POST only
happens when the coordinator is constructed ``live=True`` *and* a transport is
available — and even then ``httpx`` is imported lazily with a stdlib fallback, so
importing or testing this package never touches a socket.

The wire model is pure dataclasses (:class:`District`, :class:`AidRequest`,
:class:`AidOffer`) that round-trip to/from a :class:`~disastermind.core.contracts.Message`
payload, so a request or offer can ride the same bus as everything else.
"""
from __future__ import annotations

from .model import (
    AidDecision,
    AidOffer,
    AidRequest,
    District,
    offer_from_message,
    offer_to_message,
    request_from_message,
    request_to_message,
)
from .coordinator import AidTicket, MutualAidCoordinator, spare_from_assets

__all__ = [
    "District",
    "AidRequest",
    "AidOffer",
    "AidDecision",
    "MutualAidCoordinator",
    "AidTicket",
    "spare_from_assets",
    "request_to_message",
    "request_from_message",
    "offer_to_message",
    "offer_from_message",
]
