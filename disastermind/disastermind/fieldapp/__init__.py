"""Field-app contract + mock client (PRD Step 8 "React Native field app", Step 6).

This package defines the *device-facing* edge of DisasterMind: the small set of
dataclass contracts a field device (NDRF/SDRF team tablet, boat/heli console)
exchanges with the coordination backbone, plus a :class:`MockFieldClient` that
stands in for the real React-Native app in tests, demos and offline runs.

The mock client closes the field loop on the in-memory bus:

  * it subscribes to :data:`~disastermind.core.contracts.Topic.DISPATCH` (and,
    optionally, ``Topic.FIELD_ORDER``),
  * when its team is named in a dispatched order it AUTO-ACKs by
      (i) emitting a :data:`~disastermind.core.contracts.Topic.IOT_TELEMETRY`
          ``gps_beacon`` frame whose shape the Tier-2 field coordinator already
          consumes (``kind="gps_beacon"``, ``readings=[{team_id, asset_type,
          location, status}]``), advancing its status idle -> enroute -> onsite,
      (ii) publishing an acknowledgement :class:`~disastermind.core.contracts.Message`
           back to the dispatcher.

Everything here is stdlib-only and OPTIONAL: :func:`build.build_agents` returns
``[]`` by default and is never auto-wired into ``build_system``. A demo client is
only created when explicit team ids are supplied.
"""
from __future__ import annotations

from .contracts import (
    FIELDAPP_ACK,
    DeploymentOrderMsg,
    OrderAck,
    SiteOverCapacityReport,
    TeamStatusUpdate,
)
from .client import MockFieldClient

__all__ = [
    "DeploymentOrderMsg",
    "TeamStatusUpdate",
    "OrderAck",
    "SiteOverCapacityReport",
    "MockFieldClient",
    "FIELDAPP_ACK",
]
