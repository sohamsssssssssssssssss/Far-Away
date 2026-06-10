"""Tier 2 — Field Coordination Agent (PRD Step 6).

One :class:`FieldCoordinationAgent` owns the real-time picture of every
NDRF/SDRF team, boat and helicopter in an incident. It:

  * subscribes to ``RESOURCE_PLAN`` (deployment orders), ``ROUTING_PLAN``
    (evacuation routes) and ``IOT_TELEMETRY`` (60 s GPS beacons, PRD Step 6),
  * tracks each :class:`~disastermind.models.domain.FieldTeam` from its beacons,
  * fuses the resource plan (what asset goes where) with the routing plan (the
    waypoints to get there) into per-team field orders,
  * detects a team that has stopped moving for too long and flags a possible
    field incident,
  * AUTONOMOUSLY reassigns teams when a higher-priority rescue appears and
    requests extra resources when a team reports its site over capacity,
  * routes orders for zero-coverage zones over Iridium satellite messaging
    (``channel = "iridium"``), terrestrial elsewhere,
  * sets an escalation HINT on the published ``FIELD_ORDER`` when an order
    implies mass evacuation (>10 000), a cross-state move, or requisition of
    private infrastructure so the Tier 1 Commander can apply the authority
    matrix (PRD Step 7). The agent never escalates by itself — it only hints.

Heavy/optional libraries are imported lazily with a stdlib fallback so the
package imports and the test-suite runs with stdlib only (PRD Step 10).
"""
from __future__ import annotations

import math
import uuid
from dataclasses import asdict
from datetime import UTC
from typing import Any

from ...core.agent import BaseAgent
from ...core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
    utcnow_iso,
)
from ...models.domain import AssetType, FieldTeam
from ...models.geo import LatLon

# --------------------------------------------------------------------------- tuning
#: Beacon arrives every 60 s; if no fresh beacon / no movement for this many
#: seconds while a team is meant to be moving, flag a possible incident.
STALL_SECONDS = 180
#: Movement smaller than this (metres) between beacons counts as "not moving".
STALL_DISTANCE_M = 25.0
#: Mass-evacuation threshold that forces a commander review (PRD Step 7).
MASS_EVAC_THRESHOLD = 10_000


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


def _parse_iso_seconds(ts: str | None) -> float | None:
    """Best-effort ISO-8601 -> POSIX seconds, stdlib-only."""
    if not ts:
        return None
    try:
        from datetime import datetime

        cleaned = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except Exception:
        return None


class FieldCoordinationAgent(BaseAgent):
    """Tier 2 autonomous field coordinator (PRD Step 6).

    Tier 2 has decision authority (it reassigns teams and requests resources on
    its own) but it does NOT own the authority matrix; mass-evacuation,
    cross-state and private-requisition implications are surfaced as escalation
    hints for Tier 1.
    """

    tier = Tier.SPECIALIST
    decision_authority = True

    def __init__(self, bus, logger=None, settings=None, name: str = "field_coordinator"):
        self.settings = settings
        #: team_id -> FieldTeam (real-time state from GPS beacons, PRD Step 6).
        self.teams: dict[str, FieldTeam] = {}
        #: team_id -> {"lat","lon","secs"} last beacon snapshot (stall detection).
        self._last_beacon: dict[str, dict[str, float]] = {}
        #: team_id -> active order dict currently dispatched to that team.
        self._active_orders: dict[str, dict[str, Any]] = {}
        #: incident_id -> {"orders":[DeploymentOrder dict...], "gaps":[...]}.
        self._resource_plan: dict[str, dict[str, Any]] = {}
        #: incident_id -> [EvacRoute dict...] (waypoints per vehicle).
        self._routing_plan: dict[str, list[dict[str, Any]]] = {}
        #: team_ids already flagged stalled so we don't re-flag every tick.
        self._flagged_stalled: set[str] = set()
        super().__init__(
            name=name,
            bus=bus,
            logger=logger,
            subscriptions=[
                Topic.RESOURCE_PLAN,
                Topic.ROUTING_PLAN,
                Topic.IOT_TELEMETRY,
            ],
        )

    # ------------------------------------------------------------------ routing
    def handle(self, message: Message) -> list[Message]:
        """Dispatch inbound messages by topic (PRD Step 6)."""
        if message.topic == Topic.IOT_TELEMETRY:
            return self._on_telemetry(message)
        if message.topic == Topic.RESOURCE_PLAN:
            return self._on_resource_plan(message)
        if message.topic == Topic.ROUTING_PLAN:
            return self._on_routing_plan(message)
        return []

    # ------------------------------------------------------------- GPS beacons
    def _on_telemetry(self, message: Message) -> list[Message]:
        """Update team state from 60 s GPS beacons; detect stalled teams."""
        payload = message.payload or {}
        kind = str(payload.get("kind", ""))
        readings = payload.get("readings") or []
        # Only GPS-beacon telemetry concerns field teams; ignore weather/water etc.
        if kind not in ("gps", "gps_beacon", "beacon", "team_gps", "ais") and not any(isinstance(r, dict) and "team_id" in r for r in readings):
            return []

        now = _parse_iso_seconds(message.timestamp) or 0.0
        out: list[Message] = []
        for r in readings:
            if not isinstance(r, dict):
                continue
            team_id = r.get("team_id") or r.get("asset_id")
            loc = _as_latlon(r.get("location") or r.get("latlon") or r)
            if not team_id or loc is None:
                continue
            beacon_secs = _parse_iso_seconds(r.get("ts") or r.get("last_update")) or now
            # Capture the PRIOR beacon before overwriting it, so stall detection
            # compares this beacon against the previous one (not against itself).
            prev_beacon = self._last_beacon.get(str(team_id))
            self._record_beacon(str(team_id), r, loc, beacon_secs)
            stall = self._check_stall(str(team_id), loc, beacon_secs, prev_beacon)
            if stall is not None:
                out.append(stall)

        # If a team reports its site over capacity, autonomously request more.
        for r in readings:
            if isinstance(r, dict) and r.get("site_over_capacity"):
                extra = self._request_extra_resources(message, r)
                if extra is not None:
                    out.append(extra)
        return out

    def _record_beacon(
        self, team_id: str, reading: dict[str, Any], loc: LatLon, secs: float
    ) -> None:
        """Upsert the live :class:`FieldTeam` record from a beacon."""
        asset_type = self._coerce_asset_type(reading.get("asset_type"))
        status = str(reading.get("status") or self._infer_status(team_id))
        assignment = reading.get("assignment") or self._active_assignment(team_id)
        self.teams[team_id] = FieldTeam(
            team_id=team_id,
            asset_type=asset_type,
            location=loc,
            last_update=utcnow_iso(),
            status=status,
            assignment=assignment,
        )
        self._last_beacon[team_id] = {"lat": loc.lat, "lon": loc.lon, "secs": secs}

    @staticmethod
    def _coerce_asset_type(value: Any) -> AssetType:
        if isinstance(value, AssetType):
            return value
        if isinstance(value, str):
            try:
                return AssetType(value)
            except ValueError:
                pass
        return AssetType.NDRF_TEAM

    def _infer_status(self, team_id: str) -> str:
        if team_id in self._active_orders:
            return "enroute"
        prev = self.teams.get(team_id)
        return prev.status if prev else "idle"

    def _active_assignment(self, team_id: str) -> str | None:
        order = self._active_orders.get(team_id)
        return order.get("site") if order else None

    def _check_stall(
        self,
        team_id: str,
        loc: LatLon,
        secs: float,
        prev: dict[str, float] | None = None,
    ) -> Message | None:
        """Flag a possible field incident when an en-route team stops moving.

        ``prev`` is the beacon snapshot recorded *before* the current one; it is
        passed in explicitly because the live ``self._last_beacon`` entry has
        already been overwritten with the current fix by the time we are called.
        """
        order = self._active_orders.get(team_id)
        if not order:
            self._flagged_stalled.discard(team_id)
            return None
        team = self.teams.get(team_id)
        # A team that has reached its site is allowed to be stationary.
        if team and team.status in ("onsite", "returning", "idle"):
            self._flagged_stalled.discard(team_id)
            return None

        if not prev:
            return None
        moved = loc.distance_m(LatLon(prev["lat"], prev["lon"]))
        dt = secs - prev.get("secs", secs)
        # Need both: stationary AND enough elapsed time to be suspicious.
        if moved <= STALL_DISTANCE_M and dt >= STALL_SECONDS:
            if team_id in self._flagged_stalled:
                return None
            self._flagged_stalled.add(team_id)
            return self._build_stall_order(team_id, order, moved, dt)
        if moved > STALL_DISTANCE_M:
            self._flagged_stalled.discard(team_id)
        return None

    def _build_stall_order(
        self, team_id: str, order: dict[str, Any], moved: float, dt: float
    ) -> Message:
        """Emit a FIELD_ORDER flagging the stalled team for the commander."""
        flagged = dict(order)
        flagged["priority"] = min(int(order.get("priority", 3)), Priority.HIGH)
        flagged["reason"] = (
            f"STALL DETECTED: team {team_id} moved {moved:.0f}m in {dt:.0f}s while "
            f"en route — possible field incident; verify status."
        )
        flagged["flag"] = "possible_incident"
        return self._emit_field_order(
            incident_id=order.get("incident_id"),
            orders=[flagged],
            reasoning=[
                f"team {team_id} stalled ({moved:.0f}m/{dt:.0f}s) en route to "
                f"{order.get('site')}",
                "flagging possible field incident (PRD Step 6)",
            ],
            priority=Priority.HIGH,
            escalation=None,
        )

    def _request_extra_resources(
        self, message: Message, reading: dict[str, Any]
    ) -> Message | None:
        """AUTONOMOUS: ask the resource agent for more when a site is over capacity."""
        team_id = str(reading.get("team_id") or reading.get("asset_id") or "unknown")
        site = reading.get("site") or self._active_assignment(team_id) or "unknown"
        shortfall = int(reading.get("shortfall", 1) or 1)
        body = {
            "kind": "resource_request",
            "incident_id": message.incident_id,
            "from_team": team_id,
            "site": site,
            "shortfall": shortfall,
            "note": "site over capacity — field coordinator requesting reinforcement",
        }
        req = Message(
            sender=self.name,
            recipient="resource",
            type=MessageType.QUERY,
            priority=Priority.HIGH,
            payload=body,
            topic=Topic.RESOURCE_PLAN,
            incident_id=message.incident_id,
            module=message.module,
            reasoning=[
                f"team {team_id} reports {site} over capacity by {shortfall}",
                "autonomously requesting extra resources (PRD Step 6)",
            ],
        )
        return req

    # ----------------------------------------------------------- plan ingestion
    def _on_resource_plan(self, message: Message) -> list[Message]:
        """Store the latest deployment orders; (re)generate field orders."""
        payload = message.payload or {}
        # Ignore our own resource_request echoes if they round-trip the topic.
        if payload.get("kind") == "resource_request":
            return []
        incident_id = message.incident_id or payload.get("incident_id")
        if incident_id is None:
            return []
        self._resource_plan[incident_id] = {
            "orders": list(payload.get("orders") or []),
            "gaps": list(payload.get("gaps") or []),
        }
        return self._generate_field_orders(incident_id, message)

    def _on_routing_plan(self, message: Message) -> list[Message]:
        """Store the latest evac routes; (re)generate field orders."""
        payload = message.payload or {}
        incident_id = message.incident_id or payload.get("incident_id")
        if incident_id is None:
            return []
        self._routing_plan[incident_id] = list(payload.get("routes") or [])
        # Routing may arrive before/after resourcing; regenerate either way.
        if incident_id in self._resource_plan:
            return self._generate_field_orders(incident_id, message)
        return []

    # ----------------------------------------------------- order fusion / output
    def _generate_field_orders(self, incident_id: str, message: Message) -> list[Message]:
        """Fuse resource + routing plans into per-team field orders (PRD Step 6).

        Each deployment order is matched to an idle/lower-priority team, given
        waypoints from the routing plan, marked Iridium when its site lies in a
        zero-coverage zone, and reassignment happens automatically when a
        higher-priority rescue targets a busy team.
        """
        rplan = self._resource_plan.get(incident_id, {})
        deploy_orders = sorted(
            rplan_orders(rplan),
            key=lambda o: int(o.get("priority", 3)),
        )
        routes = self._routing_plan.get(incident_id, [])
        if not deploy_orders:
            return []

        assigned: set[str] = set()
        field_orders: list[dict[str, Any]] = []
        reasoning: list[str] = [
            f"fusing {len(deploy_orders)} deployment orders with "
            f"{len(routes)} routes for incident {incident_id}"
        ]
        mass_evac_pop = 0
        cross_state = False
        requisition = False

        for d in deploy_orders:
            team_id = self._select_team(d, assigned)
            if team_id is None:
                continue
            assigned.add(team_id)
            waypoints = self._waypoints_for(d, routes)
            site = d.get("target_cell") or d.get("site") or "unknown"
            priority = int(d.get("priority", 3))
            channel = self._channel_for(d, team_id)
            reassigned = self._is_reassignment(team_id, d)

            order = {
                "team_id": team_id,
                "site": site,
                "waypoints": waypoints,
                "priority": priority,
                "reason": d.get("reason", "deployment"),
                "channel": channel,
                "incident_id": incident_id,
                "order_id": d.get("order_id") or f"fo-{uuid.uuid4().hex[:8]}",
                "asset_id": d.get("asset_id"),
            }
            if reassigned:
                order["reason"] = (
                    f"REASSIGNED to higher-priority rescue: {order['reason']}"
                )
                reasoning.append(
                    f"reassigned {team_id} -> {site} (priority {priority})"
                )
            field_orders.append(order)
            self._active_orders[team_id] = order
            self._mark_enroute(team_id, site)

            # Accumulate escalation signals (PRD Step 7 authority matrix inputs).
            mass_evac_pop += int(d.get("population", 0) or 0)
            if d.get("cross_state"):
                cross_state = True
            if d.get("requisition_private") or d.get("private_infrastructure"):
                requisition = True

        # Mass-evac signal can also come from routing-plan population classes.
        mass_evac_pop += self._route_population(routes)

        escalation = self._build_escalation_hint(
            mass_evac_pop=mass_evac_pop,
            cross_state=cross_state,
            requisition=requisition,
            incident_id=incident_id,
            n_orders=len(field_orders),
        )
        if not field_orders:
            return []
        priority = min((o["priority"] for o in field_orders), default=int(Priority.MEDIUM))
        return [
            self._emit_field_order(
                incident_id=incident_id,
                orders=field_orders,
                reasoning=reasoning,
                priority=Priority(min(max(priority, 1), 5)),
                escalation=escalation,
                module=message.module,
            )
        ]

    def _select_team(self, deploy: dict[str, Any], assigned: set[str]) -> str | None:
        """Pick the best free team for a deployment order, else reassign.

        Preference order:
          1. the asset already named in the order (asset_id == team_id),
          2. nearest idle/enroute team of a compatible asset type,
          3. a busy team on a strictly lower-priority job (autonomous reassign).
        """
        wanted_type = self._coerce_asset_type(deploy.get("asset_type"))
        named = deploy.get("asset_id")
        if named and named not in assigned and named in self.teams:
            return str(named)

        target = self._order_location(deploy)
        prio = int(deploy.get("priority", 3))

        free = self._candidate_teams(wanted_type, deploy, assigned, busy=False)
        if free:
            return self._nearest(free, target)

        # No free team — try to preempt a lower-priority assignment.
        busy = self._candidate_teams(wanted_type, deploy, assigned, busy=True)
        preemptable = [
            t for t in busy
            if int(self._active_orders.get(t, {}).get("priority", 5)) > prio
        ]
        if preemptable:
            return self._nearest(preemptable, target)

        # As a last resort, assign any free team regardless of type match.
        any_free = [
            t for t in self.teams
            if t not in assigned and t not in self._active_orders
        ]
        if any_free:
            return self._nearest(any_free, target)
        return None

    def _candidate_teams(
        self,
        wanted_type: AssetType,
        deploy: dict[str, Any],
        assigned: set[str],
        busy: bool,
    ) -> list[str]:
        out: list[str] = []
        for tid, team in self.teams.items():
            if tid in assigned:
                continue
            is_busy = tid in self._active_orders
            if busy != is_busy:
                continue
            if team.asset_type != wanted_type:
                continue
            out.append(tid)
        return out

    def _nearest(self, team_ids: list[str], target: LatLon | None) -> str | None:
        if not team_ids:
            return None
        if target is None:
            return sorted(team_ids)[0]
        return min(
            team_ids,
            key=lambda t: self._distance(self.teams[t].location, target),
        )

    @staticmethod
    def _distance(a: LatLon, b: LatLon) -> float:
        try:
            return a.distance_m(b)
        except Exception:
            return math.inf

    def _is_reassignment(self, team_id: str, deploy: dict[str, Any]) -> bool:
        prev = self._active_orders.get(team_id)
        if not prev:
            return False
        new_site = deploy.get("target_cell") or deploy.get("site")
        return bool(new_site) and prev.get("site") != new_site

    def _order_location(self, deploy: dict[str, Any]) -> LatLon | None:
        return _as_latlon(deploy.get("location") or deploy.get("centroid"))

    def _waypoints_for(
        self, deploy: dict[str, Any], routes: list[dict[str, Any]]
    ) -> list[dict[str, float]]:
        """Pull waypoints from a matching evac route; fall back to the site point."""
        asset_id = deploy.get("asset_id")
        site = deploy.get("target_cell") or deploy.get("site")
        match = None
        for route in routes:
            if not isinstance(route, dict):
                continue
            if asset_id and route.get("vehicle_id") == asset_id:
                match = route
                break
            if site and (route.get("shelter_id") == site or route.get("route_id") == site):
                match = route
        if match is not None:
            wps = []
            for w in match.get("waypoints") or []:
                ll = _as_latlon(w)
                if ll is not None:
                    wps.append({"lat": ll.lat, "lon": ll.lon})
            if wps:
                return wps
        loc = self._order_location(deploy)
        return [{"lat": loc.lat, "lon": loc.lon}] if loc else []

    def _channel_for(self, deploy: dict[str, Any], team_id: str) -> str:
        """Iridium satellite for zero-coverage zones, terrestrial otherwise."""
        if deploy.get("zero_coverage") or deploy.get("no_cell_coverage"):
            return "iridium"
        if deploy.get("channel"):
            return str(deploy["channel"])
        # Helicopters and boats often operate beyond cell range — default sat.
        team = self.teams.get(team_id)
        if team and team.asset_type in (AssetType.HELICOPTER, AssetType.BOAT):
            return "iridium"
        return "terrestrial"

    @staticmethod
    def _route_population(routes: list[dict[str, Any]]) -> int:
        total = 0
        for route in routes:
            if isinstance(route, dict):
                total += int(route.get("population", 0) or 0)
        return total

    def _mark_enroute(self, team_id: str, site: str) -> None:
        team = self.teams.get(team_id)
        if team is not None:
            self.teams[team_id] = FieldTeam(
                team_id=team.team_id,
                asset_type=team.asset_type,
                location=team.location,
                last_update=utcnow_iso(),
                status="enroute",
                assignment=site,
            )
        self._flagged_stalled.discard(team_id)

    # --------------------------------------------------------- escalation hints
    def _build_escalation_hint(
        self,
        mass_evac_pop: int,
        cross_state: bool,
        requisition: bool,
        incident_id: str,
        n_orders: int,
    ) -> dict[str, Any] | None:
        """Set the escalation hint so the commander can apply the matrix (Step 7).

        Tier 2 never executes these — it only annotates. Mass evacuation
        (>10 000) takes precedence, then cross-state resource, then private
        requisition.
        """
        if mass_evac_pop > MASS_EVAC_THRESHOLD:
            return {
                "trigger": EscalationTrigger.MASS_EVACUATION.value,
                "summary": (
                    f"field orders imply evacuation of ~{mass_evac_pop} people "
                    f"(> {MASS_EVAC_THRESHOLD}) for incident {incident_id}"
                ),
                "scale": mass_evac_pop,
            }
        if cross_state:
            return {
                "trigger": EscalationTrigger.CROSS_STATE_RESOURCE.value,
                "summary": (
                    f"field order requires cross-state team movement for "
                    f"incident {incident_id}"
                ),
                "scale": n_orders,
            }
        if requisition:
            return {
                "trigger": EscalationTrigger.REQUISITION_PRIVATE.value,
                "summary": (
                    f"field order requires requisition of private infrastructure "
                    f"for incident {incident_id}"
                ),
                "scale": n_orders,
            }
        return None

    # ------------------------------------------------------------------- output
    def _emit_field_order(
        self,
        incident_id: str | None,
        orders: list[dict[str, Any]],
        reasoning: list[str],
        priority: Priority,
        escalation: dict[str, Any] | None,
        module: Module = Module.ALL,
    ) -> Message:
        """Construct a FIELD_ORDER message per the wire convention (PRD Step 6)."""
        trigger = None
        if escalation:
            try:
                trigger = EscalationTrigger(escalation["trigger"])
            except (ValueError, KeyError):
                trigger = None
        payload = {
            "kind": "field_order",
            "incident_id": incident_id,
            "orders": orders,
            "escalation": escalation,
        }
        return Message(
            sender=self.name,
            recipient="commander",
            type=MessageType.INSTRUCTION,
            priority=priority,
            payload=payload,
            topic=Topic.FIELD_ORDER,
            incident_id=incident_id,
            module=module,
            escalation_trigger=trigger,
            reasoning=reasoning,
        )

    # --------------------------------------------------------------------- tick
    def tick(self) -> list[Message]:
        """Periodic sweep: re-check stall state from last-known beacons (Step 10).

        Runs on the 30 s coordination loop. A team whose last beacon is now
        older than ``STALL_SECONDS`` while still en route is flagged even if no
        new telemetry arrived (silent-radio / lost-beacon case).
        """
        from datetime import datetime

        now = datetime.now(UTC).timestamp()
        out: list[Message] = []
        for team_id, order in list(self._active_orders.items()):
            team = self.teams.get(team_id)
            if team and team.status in ("onsite", "returning", "idle"):
                continue
            prev = self._last_beacon.get(team_id)
            if not prev:
                continue
            silent = now - prev.get("secs", now)
            if silent >= STALL_SECONDS and team_id not in self._flagged_stalled:
                self._flagged_stalled.add(team_id)
                flagged = dict(order)
                flagged["priority"] = min(int(order.get("priority", 3)), Priority.HIGH)
                flagged["reason"] = (
                    f"SILENT BEACON: no GPS update from team {team_id} for "
                    f"{silent:.0f}s — possible field incident; verify status."
                )
                flagged["flag"] = "possible_incident"
                out.append(
                    self._emit_field_order(
                        incident_id=order.get("incident_id"),
                        orders=[flagged],
                        reasoning=[
                            f"team {team_id} beacon silent {silent:.0f}s en route",
                            "flagging possible field incident (PRD Step 6/10)",
                        ],
                        priority=Priority.HIGH,
                        escalation=None,
                    )
                )
        return out

    # ------------------------------------------------------------- introspection
    def snapshot(self) -> dict[str, Any]:
        """Serialisable live view of all tracked teams (debug / dry-run)."""
        return {
            "teams": {tid: asdict(t) for tid, t in self.teams.items()},
            "active_orders": dict(self._active_orders),
            "flagged_stalled": sorted(self._flagged_stalled),
        }


def rplan_orders(rplan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deployment-order dicts from a stored resource plan, safely."""
    orders = rplan.get("orders") if isinstance(rplan, dict) else None
    return [o for o in (orders or []) if isinstance(o, dict)]
