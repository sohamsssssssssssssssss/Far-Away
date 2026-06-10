"""Close the dispatch -> ACK -> GPS -> field-coordination loop (PRD Step 6/8).

This module wires the *device edge* back into the autonomous backbone so a full
control loop can be observed end-to-end:

    Tier-1 Commander ──DISPATCH──▶ MockFieldClient (this team's device)
                                      │  auto-ACKs the order, emitting…
                                      ├─▶ IOT_TELEMETRY gps_beacon  ──▶ Tier-2
                                      │     (status idle→enroute→onsite)   field
                                      │                                    coord.
                                      └─▶ FIELDAPP_ACK OrderAck     ──▶ dispatcher

:func:`attach_field_clients` registers one :class:`~.client.MockFieldClient` per
field team onto an existing loop's bus. Each client already subscribes to
:data:`~disastermind.core.contracts.Topic.DISPATCH` on construction, so once
attached the loop is *closed*: any Commander dispatch addressed to a team makes
that team's client (i) advance its status one step along ``idle -> enroute ->
onsite`` and publish the matching ``kind="gps_beacon"`` telemetry frame the
Tier-2 :class:`FieldCoordinationAgent` consumes, and (ii) publish an
:class:`~.contracts.OrderAck` back on :data:`~.contracts.FIELDAPP_ACK`.

OPT-IN ONLY. Nothing here is auto-wired into ``build_system`` — a closed loop
exists solely because the caller chose to attach clients. With no clients
attached the backbone behaves exactly as before, so the existing suite is
unaffected. Stdlib-only; no network I/O.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..core.bus import MessageBus
from ..models.domain import AssetType
from ..models.geo import LatLon
from .build import build_clients
from .client import MockFieldClient


def attach_field_clients(
    loop: Any,
    team_ids: Iterable[Any] | None = None,
    *,
    subscribe_field_orders: bool = False,
) -> list[MockFieldClient]:
    """Register a :class:`MockFieldClient` per field team onto ``loop.bus``.

    Closes the dispatch -> ACK -> GPS -> field-coordination loop (PRD Step 6/8):
    after this call, any ``Topic.DISPATCH`` order the Commander emits for one of
    the given teams is auto-ACKed by that team's client, which emits both a
    ``gps_beacon`` telemetry frame (advancing ``idle -> enroute -> onsite``, the
    exact shape :class:`FieldCoordinationAgent` consumes) and an
    :class:`~.contracts.OrderAck` on :data:`~.contracts.FIELDAPP_ACK`.

    Parameters
    ----------
    loop:
        A :class:`~disastermind.orchestration.loop.CoordinationLoop` (anything
        exposing ``.bus`` and, optionally, ``.logger`` / ``.agents``). The
        clients attach to ``loop.bus`` so they share the in-memory fan-out with
        the rest of the DAG.
    team_ids:
        Teams to attach a device for. Each entry is either a ``team_id`` string
        or a ``(team_id, asset_type[, lat, lon])`` tuple. When ``None`` the
        roster is discovered automatically (see :func:`_resolve_team_specs`):
        teams the loop's field coordinator already tracks, else the standard
        scenario roster — so attaching to a driven scenario "just works".
    subscribe_field_orders:
        Also service ``Topic.FIELD_ORDER`` orders (off by default; the Commander
        dispatches on ``Topic.DISPATCH``).

    Returns
    -------
    list[MockFieldClient]
        The attached clients (already live on the bus), in roster order. Returns
        ``[]`` when no teams can be resolved — a no-op that leaves the loop
        exactly as it was.
    """
    bus = _loop_bus(loop)
    if bus is None:
        return []
    specs = _resolve_team_specs(loop, team_ids)
    if not specs:
        return []
    logger = getattr(loop, "logger", None)
    return build_clients(
        bus,
        specs,
        logger=logger,
        subscribe_field_orders=subscribe_field_orders,
    )


# --------------------------------------------------------------------- internals
def _loop_bus(loop: Any) -> MessageBus | None:
    """Return the loop's bus, accepting a bare bus for convenience."""
    if isinstance(loop, MessageBus):
        return loop
    bus = getattr(loop, "bus", None)
    return bus if isinstance(bus, MessageBus) else None


def _resolve_team_specs(loop: Any, team_ids: Iterable[Any] | None) -> list[Any]:
    """Resolve the team roster to attach clients for.

    Resolution order (first non-empty wins):
      1. explicit ``team_ids`` (strings or ``(id, asset[, lat, lon])`` tuples);
      2. teams the loop's :class:`FieldCoordinationAgent` already tracks (so a
         seeded/driven scenario contributes its real roster with live asset
         types and positions);
      3. the standard scenario roster (``scenarios.base.DEFAULT_TEAMS``).
    """
    if team_ids is not None:
        return list(team_ids)
    from_coord = _specs_from_coordinator(loop)
    if from_coord:
        return from_coord
    return _default_roster()


def _specs_from_coordinator(loop: Any) -> list[Any]:
    """Build specs from teams a field coordinator on the loop already tracks."""
    specs: list[Any] = []
    for agent in getattr(loop, "agents", None) or []:
        teams = getattr(agent, "teams", None)
        if not isinstance(teams, dict) or not teams:
            continue
        for team_id, team in teams.items():
            asset_type = getattr(team, "asset_type", None)
            asset = asset_type.value if isinstance(asset_type, AssetType) else "ndrf_team"
            loc = getattr(team, "location", None)
            if isinstance(loc, LatLon):
                specs.append((str(team_id), asset, loc.lat, loc.lon))
            else:
                specs.append((str(team_id), asset))
        # First coordinator with teams wins; field state is authoritative.
        if specs:
            break
    return specs


def _default_roster() -> list[Any]:
    """The standard pre-positioned scenario roster (lazy import; optional)."""
    try:
        from ..scenarios.base import DEFAULT_TEAMS
    except Exception:
        return []
    return [tuple(t) for t in DEFAULT_TEAMS]
