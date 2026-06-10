"""Device-facing dataclass contracts for the field app (PRD Step 8 / Step 6).

These mirror the codebase's typed-dataclass style (cf. ``models/domain.py``):
plain, ``dataclasses.asdict``-serialisable structures that ride inside a
:class:`~disastermind.core.contracts.Message` payload. They are the wire format
between a field device and the coordination backbone:

  * :class:`DeploymentOrderMsg`     — backbone -> device: "go here, do this".
  * :class:`TeamStatusUpdate`       — device -> backbone: a 60 s GPS beacon
                                      (PRD Step 6) carrying the team's live
                                      position and status (idle/enroute/onsite).
  * :class:`OrderAck`               — device -> backbone: explicit receipt of an
                                      order (acknowledgement).
  * :class:`SiteOverCapacityReport` — device -> backbone: the on-scene team
                                      reports its site over capacity so the
                                      Tier-2 coordinator can autonomously request
                                      reinforcement (PRD Step 6).

A field-app topic constant is declared HERE (per the package-isolation rule) so
the package never edits :mod:`disastermind.core.contracts`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.contracts import utcnow_iso
from ..models.domain import AssetType
from ..models.geo import LatLon

#: Topic the mock client publishes explicit order acknowledgements on. Declared
#: locally to honour the package-isolation rule (never edit core.contracts).
FIELDAPP_ACK = "fieldapp.order_ack"

#: Status lifecycle a device walks through as it services an order (PRD Step 6).
STATUS_FLOW: tuple[str, ...] = ("idle", "enroute", "onsite", "returning")


@dataclass
class DeploymentOrderMsg:
    """Backbone -> device deployment order (PRD Step 8 field app).

    A thin device-facing projection of the Tier-2 field order / Tier-1 dispatch:
    enough for the app to render "team X, go to <site>, follow these waypoints".
    """

    order_id: str
    team_id: str
    site: str
    priority: int = 3
    reason: str = ""
    waypoints: list[dict[str, float]] = field(default_factory=list)
    channel: str = "terrestrial"
    incident_id: str | None = None

    @classmethod
    def from_payload(
        cls, order: dict[str, Any], incident_id: str | None = None
    ) -> DeploymentOrderMsg:
        """Project a raw dispatch/field-order dict onto the device contract.

        Accepts the order dict embedded in a ``Topic.DISPATCH`` payload
        (``payload["order"]``) or a bare Tier-2 field-order dict.
        """
        return cls(
            order_id=str(order.get("order_id") or order.get("id") or "unknown"),
            team_id=str(order.get("team_id") or "unassigned"),
            site=str(order.get("site") or order.get("target_cell") or "unknown"),
            priority=int(order.get("priority", 3) or 3),
            reason=str(order.get("reason", "")),
            waypoints=[
                w for w in (order.get("waypoints") or []) if isinstance(w, dict)
            ],
            channel=str(order.get("channel", "terrestrial")),
            incident_id=incident_id or order.get("incident_id"),
        )


@dataclass
class TeamStatusUpdate:
    """Device -> backbone GPS beacon (PRD Step 6, 60 s cadence).

    Serialises (via :meth:`to_reading`) into exactly the per-team reading shape
    the Tier-2 field coordinator consumes: ``{team_id, asset_type, location,
    status}`` inside a ``kind="gps_beacon"`` telemetry frame.
    """

    team_id: str
    asset_type: AssetType
    location: LatLon
    status: str = "idle"  # idle | enroute | onsite | returning
    assignment: str | None = None
    ts: str = field(default_factory=utcnow_iso)

    def to_reading(self) -> dict[str, Any]:
        """Render the single beacon reading the field coordinator expects."""
        return {
            "team_id": self.team_id,
            "asset_type": self.asset_type.value,
            "location": {"lat": self.location.lat, "lon": self.location.lon},
            "status": self.status,
            "assignment": self.assignment,
            "ts": self.ts,
        }


@dataclass
class OrderAck:
    """Device -> backbone explicit order receipt (acknowledgement)."""

    order_id: str
    team_id: str
    status: str  # accepted | rejected | completed
    note: str = ""
    incident_id: str | None = None
    ts: str = field(default_factory=utcnow_iso)


@dataclass
class SiteOverCapacityReport:
    """Device -> backbone over-capacity report (PRD Step 6).

    Maps onto a GPS-beacon reading carrying ``site_over_capacity=True`` so the
    Tier-2 coordinator autonomously requests reinforcement.
    """

    team_id: str
    site: str
    shortfall: int = 1
    note: str = "site over capacity"
    incident_id: str | None = None
    ts: str = field(default_factory=utcnow_iso)

    def to_reading(self, location: LatLon, asset_type: AssetType) -> dict[str, Any]:
        """Render an over-capacity beacon reading for the IoT telemetry frame."""
        return {
            "team_id": self.team_id,
            "asset_type": asset_type.value,
            "location": {"lat": location.lat, "lon": location.lon},
            "status": "onsite",
            "site": self.site,
            "site_over_capacity": True,
            "shortfall": self.shortfall,
            "note": self.note,
            "ts": self.ts,
        }
