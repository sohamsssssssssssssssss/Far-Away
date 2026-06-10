"""Factory for the OPTIONAL field-app module (PRD Step 8 / Step 6).

The field app is a *device* edge, not part of the autonomous coordination DAG,
so this factory is deliberately **never** auto-wired into ``build_system``. To
keep the uniform module contract (``build_agents(bus, logger, settings) -> list``)
it returns an EMPTY list by default — wiring it in changes nothing.

Demo clients are created only when explicit team ids are supplied via
``settings`` (attribute or env-style ``DM_FIELDAPP_TEAMS`` =
``"NDRF-01:ndrf_team:20.30:85.82,BOAT-01:boat"``) or via :func:`build_clients`.
"""
from __future__ import annotations

from typing import Any

from ..core.bus import MessageBus
from ..models.domain import AssetType
from ..models.geo import LatLon
from .client import MockFieldClient


def build_agents(bus: MessageBus, logger, settings) -> list:
    """Return field-app agents — EMPTY by default (PRD Step 8, opt-in only).

    A demo client per team is created only if ``settings`` advertises team ids
    (attribute ``fieldapp_teams`` or env-style ``DM_FIELDAPP_TEAMS``); otherwise
    this returns ``[]`` so it can be listed in a build order without side effects.
    """
    specs = _team_specs_from_settings(settings)
    if not specs:
        return []
    return build_clients(bus, specs, logger=logger)


def build_clients(
    bus: MessageBus,
    teams: list[Any],
    logger=None,
    subscribe_field_orders: bool = False,
) -> list[MockFieldClient]:
    """Create one :class:`MockFieldClient` per team spec (demo / tests).

    Each spec is either a ``team_id`` string or a tuple/list
    ``(team_id, asset_type[, lat, lon])``.
    """
    clients: list[MockFieldClient] = []
    for spec in teams:
        team_id, asset_type, loc = _parse_spec(spec)
        clients.append(
            MockFieldClient(
                team_id=team_id,
                bus=bus,
                asset_type=asset_type,
                location=loc,
                logger=logger,
                subscribe_field_orders=subscribe_field_orders,
            )
        )
    return clients


def _parse_spec(spec: Any) -> tuple[str, AssetType, LatLon | None]:
    if isinstance(spec, str):
        return spec, AssetType.NDRF_TEAM, None
    if isinstance(spec, (list, tuple)) and spec:
        team_id = str(spec[0])
        asset_type = _coerce_asset_type(spec[1]) if len(spec) > 1 else AssetType.NDRF_TEAM
        loc: LatLon | None = None
        if len(spec) >= 4:
            try:
                loc = LatLon(float(spec[2]), float(spec[3]))
            except (TypeError, ValueError):
                loc = None
        return team_id, asset_type, loc
    return str(spec), AssetType.NDRF_TEAM, None


def _coerce_asset_type(value: Any) -> AssetType:
    if isinstance(value, AssetType):
        return value
    if isinstance(value, str):
        try:
            return AssetType(value)
        except ValueError:
            pass
    return AssetType.NDRF_TEAM


def _team_specs_from_settings(settings) -> list[Any]:
    """Extract opt-in team specs from settings (attribute or env-style string)."""
    if settings is None:
        return []
    raw = getattr(settings, "fieldapp_teams", None)
    if raw is None:
        import os

        raw = os.environ.get("DM_FIELDAPP_TEAMS")
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    # comma-separated "team:asset:lat:lon" tuples
    specs: list[Any] = []
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        specs.append(tuple(p.strip() for p in parts) if len(parts) > 1 else parts[0])
    return specs
