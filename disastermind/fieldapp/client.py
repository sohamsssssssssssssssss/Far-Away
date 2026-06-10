"""MockFieldClient — stdlib stand-in for the React-Native field app (PRD Step 8).

A :class:`MockFieldClient` is bound to a single ``team_id`` and behaves like the
device that team carries. It is a Tier-3 *edge* participant — it has **no
decision authority** (it executes orders and reports position; it never plans).

Lifecycle on the in-memory bus:

  1. It subscribes to :data:`~disastermind.core.contracts.Topic.DISPATCH`
     (default) and, optionally, :data:`~disastermind.core.contracts.Topic.FIELD_ORDER`.
  2. When an inbound order names this client's team, it AUTO-ACKs by:
       * advancing its status one step along ``idle -> enroute -> onsite`` and
         emitting a ``kind="gps_beacon"`` :data:`Topic.IOT_TELEMETRY` frame whose
         readings shape (``{team_id, asset_type, location, status}``) the Tier-2
         field coordinator already consumes — closing the loop so the field tier
         sees fresh team state, and
       * publishing an :class:`~.contracts.OrderAck` Message on
         :data:`~.contracts.FIELDAPP_ACK` addressed back to the order's sender.

Because the client emits a GPS beacon that the coordinator ingests, it lets the
field tier observe a team transition from idle to onsite purely in response to a
dispatch — exactly the closed control loop PRD Step 6/8 describe.

This module is OPTIONAL and never auto-wired into ``build_system`` (see
:mod:`.build`). It is stdlib-only and performs no network I/O.
"""
from __future__ import annotations

import uuid
from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from ..models.domain import AssetType
from ..models.geo import LatLon
from .contracts import (
    FIELDAPP_ACK,
    STATUS_FLOW,
    DeploymentOrderMsg,
    OrderAck,
    SiteOverCapacityReport,
    TeamStatusUpdate,
)


def _as_latlon(obj: Any) -> LatLon | None:
    """Coerce a payload fragment into a :class:`LatLon` (dict or sequence)."""
    if obj is None:
        return None
    if isinstance(obj, LatLon):
        return obj
    if isinstance(obj, dict) and "lat" in obj and "lon" in obj:
        try:
            return LatLon(float(obj["lat"]), float(obj["lon"]))
        except (TypeError, ValueError):
            return None
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        try:
            return LatLon(float(obj[0]), float(obj[1]))
        except (TypeError, ValueError):
            return None
    return None


class MockFieldClient(BaseAgent):
    """A simulated field device bound to one team (PRD Step 8 / Step 6).

    Tier 3 / edge: ``decision_authority = False``. It only reports state and
    acknowledges orders; it never originates a plan.
    """

    tier = Tier.EDGE
    decision_authority = False

    def __init__(
        self,
        team_id: str,
        bus: MessageBus,
        asset_type: AssetType | str = AssetType.NDRF_TEAM,
        location: LatLon | tuple[float, float] | None = None,
        logger: DecisionLogger | None = None,
        module: Module = Module.ALL,
        subscribe_field_orders: bool = False,
        name: str | None = None,
    ) -> None:
        self.team_id = str(team_id)
        self.asset_type = self._coerce_asset_type(asset_type)
        self.location = self._coerce_location(location)
        self.module = module if isinstance(module, Module) else Module.ALL
        #: current point in STATUS_FLOW (idle/enroute/onsite/returning).
        self.status = "idle"
        self.assignment: str | None = None
        #: order_ids already serviced — re-delivery is idempotent.
        self._serviced: set[str] = set()
        subs = [Topic.DISPATCH]
        if subscribe_field_orders:
            subs.append(Topic.FIELD_ORDER)
        super().__init__(
            name=name or f"fieldapp.{self.team_id}",
            bus=bus,
            logger=logger,
            subscriptions=subs,
        )

    # ------------------------------------------------------------------ coercion
    @staticmethod
    def _coerce_asset_type(value: AssetType | str) -> AssetType:
        if isinstance(value, AssetType):
            return value
        if isinstance(value, str):
            try:
                return AssetType(value)
            except ValueError:
                pass
        return AssetType.NDRF_TEAM

    @staticmethod
    def _coerce_location(value: Any) -> LatLon:
        ll = _as_latlon(value)
        return ll if ll is not None else LatLon(0.0, 0.0)

    # ------------------------------------------------------------------- inbound
    def handle(self, message: Message) -> list[Message]:
        """Service a dispatched/field order addressed to this client's team."""
        if message.topic not in (Topic.DISPATCH, Topic.FIELD_ORDER):
            return []
        payload = message.payload or {}
        # Ignore housekeeping ACKs (e.g. the dispatch router's own receipts).
        if message.type is MessageType.ACK or payload.get("kind") in (
            "dispatch_ack",
            "order_ack",
        ):
            return []

        order = self._extract_order(payload)
        if order is None:
            return []
        order_id = str(order.get("order_id") or order.get("id") or "")
        if order_id and order_id in self._serviced:
            return []
        if order_id:
            self._serviced.add(order_id)

        dom = DeploymentOrderMsg.from_payload(order, incident_id=message.incident_id)
        return self._service_order(dom, message)

    def _extract_order(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Find the order dict in this team for a DISPATCH/FIELD_ORDER payload.

        A ``Topic.DISPATCH`` order carries a single ``payload["order"]``; a
        ``Topic.FIELD_ORDER`` carries a list under ``payload["orders"]``.
        """
        single = payload.get("order")
        if isinstance(single, dict) and self._matches(single):
            return single
        for o in payload.get("orders") or []:
            if isinstance(o, dict) and self._matches(o):
                return o
        return None

    def _matches(self, order: dict[str, Any]) -> bool:
        return str(order.get("team_id") or "") == self.team_id

    # ------------------------------------------------------------------ servicing
    def _service_order(
        self, order: DeploymentOrderMsg, source: Message
    ) -> list[Message]:
        """Advance status, emit a GPS beacon, and ACK the order's sender."""
        self.assignment = order.site
        self._advance_toward_site(order)
        beacon = self._gps_beacon(source, assignment=order.site)
        ack = self._order_ack(order, source, status="accepted")
        return [beacon, ack]

    def _advance_toward_site(self, order: DeploymentOrderMsg) -> None:
        """Move one step along the status flow and snap location to the site.

        idle -> enroute on first order; a re-issued order to a team already
        en route advances it to onsite, mirroring a device that has arrived.
        """
        idx = STATUS_FLOW.index(self.status) if self.status in STATUS_FLOW else 0
        # idle -> enroute -> onsite (cap at onsite for a deployment).
        self.status = STATUS_FLOW[min(idx + 1, STATUS_FLOW.index("onsite"))]
        # When arriving, snap the reported position to the last waypoint/site.
        if self.status == "onsite" and order.waypoints:
            ll = _as_latlon(order.waypoints[-1])
            if ll is not None:
                self.location = ll

    # -------------------------------------------------------------------- egress
    def _gps_beacon(
        self, source: Message | None = None, assignment: str | None = None
    ) -> Message:
        """Build a ``kind="gps_beacon"`` IOT_TELEMETRY frame for this team.

        The reading shape (``{team_id, asset_type, location, status}``) is the
        one :class:`~disastermind.tier2.field.agent.FieldCoordinationAgent`
        consumes (see ``_on_telemetry``).
        """
        update = TeamStatusUpdate(
            team_id=self.team_id,
            asset_type=self.asset_type,
            location=self.location,
            status=self.status,
            assignment=assignment if assignment is not None else self.assignment,
        )
        return Message(
            sender=self.name,
            recipient="broadcast",
            type=MessageType.QUERY,
            priority=Priority.INFO,
            topic=Topic.IOT_TELEMETRY,
            incident_id=source.incident_id if source else None,
            module=self.module,
            payload={"kind": "gps_beacon", "readings": [update.to_reading()]},
            reasoning=[
                f"team {self.team_id} status -> {self.status}"
                + (f" (assignment {assignment})" if assignment else "")
            ],
        )

    def _order_ack(
        self, order: DeploymentOrderMsg, source: Message, status: str = "accepted"
    ) -> Message:
        """Build an explicit OrderAck Message back to the order's sender."""
        ack = OrderAck(
            order_id=order.order_id,
            team_id=self.team_id,
            status=status,
            note=f"team {self.team_id} {status} order for {order.site}",
            incident_id=order.incident_id,
        )
        return Message(
            sender=self.name,
            recipient=source.sender or "dispatch",
            type=MessageType.ACK,
            priority=Priority.LOW,
            topic=FIELDAPP_ACK,
            incident_id=order.incident_id,
            module=self.module,
            payload={
                "kind": "order_ack",
                "order_id": order.order_id,
                "team_id": self.team_id,
                "status": status,
                "note": ack.note,
            },
            reasoning=[ack.note],
        )

    # -------------------------------------------------------- device-driven API
    def beacon(self) -> Message:
        """Emit (and publish) the current position as a GPS beacon (PRD Step 6)."""
        msg = self._gps_beacon()
        self.emit(msg)
        return msg

    def report_over_capacity(
        self, site: str | None = None, shortfall: int = 1, incident_id: str | None = None
    ) -> Message:
        """Device reports its on-scene site is over capacity (PRD Step 6).

        Emits a GPS beacon carrying ``site_over_capacity=True`` so the Tier-2
        coordinator autonomously requests reinforcement.
        """
        self.status = "onsite"
        report = SiteOverCapacityReport(
            team_id=self.team_id,
            site=site or self.assignment or "unknown",
            shortfall=shortfall,
            incident_id=incident_id,
        )
        msg = Message(
            sender=self.name,
            recipient="broadcast",
            type=MessageType.QUERY,
            priority=Priority.HIGH,
            topic=Topic.IOT_TELEMETRY,
            incident_id=incident_id,
            module=self.module,
            payload={
                "kind": "gps_beacon",
                "readings": [report.to_reading(self.location, self.asset_type)],
            },
            reasoning=[
                f"team {self.team_id} reports {report.site} over capacity "
                f"by {shortfall} (PRD Step 6)"
            ],
        )
        self.emit(msg)
        return msg

    def snapshot(self) -> dict[str, Any]:
        """Serialisable live device state (debug / tests)."""
        return {
            "team_id": self.team_id,
            "asset_type": self.asset_type.value,
            "status": self.status,
            "assignment": self.assignment,
            "location": {"lat": self.location.lat, "lon": self.location.lon},
            "serviced": sorted(self._serviced),
        }
