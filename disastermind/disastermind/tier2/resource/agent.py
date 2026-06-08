"""Tier-2 Resource Optimisation Agent (PRD Step 4).

One :class:`ResourceAllocationAgent` per running module. It

  * subscribes to ``Topic.PREDICTION`` (risk-cells / projected demand) and
    ``Topic.CASCADE`` (road/bridge failure windows that constrain reachability),
  * maintains an inventory of deployable assets (boats / helis / NDRF / SDRF /
    medical / fire / USAR) plus a vulnerability map,
  * runs the LP / greedy optimiser (:mod:`.optimizer`) under the EQUITY
    CONSTRAINT, and
  * publishes a ``Topic.RESOURCE_PLAN`` payload of
    :class:`~disastermind.models.domain.DeploymentOrder` plus
    :class:`~disastermind.models.domain.ResourceGap` alerts.

AUTONOMOUS DECISIONS (no human approval, PRD Step 7): deploy pre-positioned
resources within 50 km, reroute teams, request mutual aid from *adjacent*
districts, requisition fuel/supplies from government depots, pre-stage medical
units, pre-position boats. The agent makes these directly.

Decisions that *exceed* autonomous authority — notably cross-state / inter-state
mutual-aid requests — are NOT executed here. They are *tagged* on the relevant
order so the Tier-1 Commander can apply the escalation matrix. This agent never
escalates on its own; it only annotates.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.contracts import (
    EscalationTrigger,
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from ...audit.decision_log import DecisionLogger
from ...models.domain import (
    Asset,
    AssetType,
    CascadeFailure,
    DeploymentOrder,
    ResourceGap,
    VulnerabilityProfile,
)
from ...models.geo import LatLon
from .optimizer import (
    PREPOSITION_RADIUS_KM,
    AssetView,
    DemandCell,
    optimise,
)

# How many uncovered people in a cell escalates the deployment priority.
_HIGH_DEMAND_THRESHOLD = 500


def _latlon(obj: Any) -> LatLon:
    """Coerce a dict / LatLon into a :class:`LatLon` (payloads are JSON dicts)."""
    if isinstance(obj, LatLon):
        return obj
    if isinstance(obj, dict):
        return LatLon(float(obj.get("lat", 0.0)), float(obj.get("lon", 0.0)))
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        return LatLon(float(obj[0]), float(obj[1]))
    return LatLon(0.0, 0.0)


def _vuln(obj: Any) -> VulnerabilityProfile:
    if isinstance(obj, VulnerabilityProfile):
        return obj
    if isinstance(obj, dict):
        return VulnerabilityProfile(
            elderly_density=float(obj.get("elderly_density", 0.0)),
            hospital_proximity=float(obj.get("hospital_proximity", 0.0)),
            road_accessibility=float(obj.get("road_accessibility", 1.0)),
            informal_settlement_density=float(obj.get("informal_settlement_density", 0.0)),
            mobility_impaired=int(obj.get("mobility_impaired", 0)),
            children=int(obj.get("children", 0)),
            hospitalised=int(obj.get("hospitalised", 0)),
        )
    return VulnerabilityProfile()


class ResourceAllocationAgent(BaseAgent):
    """Equity-weighted asset allocation for one disaster module (PRD Step 4)."""

    tier = Tier.SPECIALIST
    decision_authority = True  # Tier-2 acts autonomously within its domain

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        settings: Any = None,
        name: str = "resource.allocator",
        module: Module = Module.ALL,
        assets: list[Asset] | None = None,
        vulnerability_map: dict[str, VulnerabilityProfile] | None = None,
        home_state: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            bus=bus,
            logger=logger,
            subscriptions=[Topic.PREDICTION, Topic.CASCADE],
        )
        self.settings = settings
        self.module = module
        self.home_state = home_state
        # Asset inventory keyed by id; defaults to a small pre-positioned sample.
        self.assets: dict[str, Asset] = {
            a.asset_id: a for a in (assets if assets is not None else _sample_assets())
        }
        self.vulnerability_map: dict[str, VulnerabilityProfile] = (
            vulnerability_map or {}
        )
        # Last-known cascade state (PRD Step 10: operate on last-known state).
        self._cascade_by_incident: dict[str, list[CascadeFailure]] = {}

    # ------------------------------------------------------------------ inventory
    def register_asset(self, asset: Asset) -> None:
        """Add/replace an asset in the inventory (used by orchestration/tests)."""
        self.assets[asset.asset_id] = asset

    def set_vulnerability(self, cell_id: str, profile: VulnerabilityProfile) -> None:
        self.vulnerability_map[cell_id] = profile

    # --------------------------------------------------------------------- handle
    def handle(self, message: Message) -> list[Message]:
        if message.topic == Topic.CASCADE:
            self._absorb_cascade(message)
            return []
        if message.topic == Topic.PREDICTION:
            return self._on_prediction(message)
        return []

    # ----------------------------------------------------------------- cascade in
    def _absorb_cascade(self, message: Message) -> None:
        """Record cascade failures so reachability/ETA can be constrained.

        Cascade is consumed for *context* (which segments are failing); the
        plan it co-feeds is published on PREDICTION-trigger so we just cache it.
        """
        payload = message.payload or {}
        failures = [
            _as_cascade_failure(f) for f in payload.get("failures", [])
        ]
        incident = message.incident_id or payload.get("incident_id") or "unknown"
        self._cascade_by_incident[incident] = failures

    @staticmethod
    def _cascade_penalty(failures: list[CascadeFailure]) -> float:
        """Multiplier (>=1.0) raising urgency when access routes are failing."""
        if not failures:
            return 1.0
        # Sooner-failing, more-numerous segments => greater urgency to deploy now.
        soonest = min((f.fails_at_minute for f in failures), default=10_000)
        urgency = 1.0 + min(0.5, len(failures) * 0.05)
        if soonest <= 60:
            urgency += 0.25
        return urgency

    # -------------------------------------------------------------- prediction in
    def _on_prediction(self, message: Message) -> list[Message]:
        payload = message.payload or {}
        if payload.get("kind") != "risk":
            return []
        incident = message.incident_id or payload.get("incident_id") or "unknown"
        module = _module_from(payload.get("module"), self.module)

        cells = self._build_demand(payload)
        if not cells:
            return []

        assets = self._asset_views()
        cascade = self._cascade_by_incident.get(incident, [])
        penalty = self._cascade_penalty(cascade)

        prefer_lp = self._prefer_lp()
        result = optimise(assets, cells, prefer_lp=prefer_lp)

        orders, escalate_cross_state = self._to_orders(result, cells, penalty)
        gaps = self._to_gaps(result, cells)

        reasoning = [
            f"solver={result.solver} objective={result.objective}",
            f"assets={len(assets)} demand_cells={len(cells)} cascade_failures={len(cascade)}",
            f"equity-weighted allocation (VulnerabilityProfile.weight) cascade_urgency={round(penalty, 3)}",
            f"{len(orders)} deployment orders, {len(gaps)} resource gaps",
        ]
        if escalate_cross_state:
            reasoning.append(
                f"{len(escalate_cross_state)} cross-state/mutual-aid orders tagged "
                f"for commander escalation (CROSS_STATE_RESOURCE)"
            )

        out_payload: dict[str, Any] = {
            "kind": "resource_plan",
            "incident_id": incident,
            "module": module.value,
            "solver": result.solver,
            "objective": result.objective,
            "orders": [asdict(o) for o in orders],
            "gaps": [asdict(g) for g in gaps],
            "cross_state_order_ids": escalate_cross_state,
            # Demand zones for the downstream EvacuationRoutingAgent. The routing
            # tier reads ``zones`` (population to evacuate) from the resource plan
            # to seed its VRP; without this the prediction->routing data edge is
            # dead (routing has depots from ``orders`` but no demand). We surface
            # the equity-adjusted demand cells we just optimised against.
            "zones": [self._zone_from_cell(c) for c in cells],
            # Depots (route origins): the deploying assets' real locations. The
            # DeploymentOrder schema (frozen) carries no coordinates, so we attach
            # the asset positions here. Routing reads ``depots`` directly; without
            # a depot *location* the VRP cannot place vehicles and emits no plan.
            "depots": self._depots_for(orders),
        }

        # Cross-state mutual aid exceeds autonomous authority — tag the message so
        # the Commander applies the escalation matrix (we never escalate ourselves).
        escalation_trigger = (
            EscalationTrigger.CROSS_STATE_RESOURCE if escalate_cross_state else None
        )
        priority = Priority.CRITICAL if any(
            o.priority <= 2 for o in orders
        ) else Priority.HIGH

        return [
            Message(
                sender=self.name,
                recipient="broadcast",
                type=MessageType.INSTRUCTION,
                priority=priority,
                payload=out_payload,
                reasoning=reasoning,
                topic=Topic.RESOURCE_PLAN,
                incident_id=incident,
                module=module,
                escalation_trigger=escalation_trigger,
            )
        ]

    # ------------------------------------------------------------------ builders
    def _depots_for(self, orders: list[DeploymentOrder]) -> list[dict[str, Any]]:
        """Build routing depots (vehicle origins) from the deployment orders.

        Each order names an ``asset_id``; we look the asset up in our inventory
        to recover its real location and capacity so the routing tier can place
        the vehicle. Assets missing from inventory are skipped (no location).
        """
        depots: list[dict[str, Any]] = []
        for o in orders:
            asset = self.assets.get(o.asset_id)
            if asset is None:
                continue
            depots.append(
                {
                    "vehicle_id": asset.asset_id,
                    "depot": {"lat": asset.location.lat, "lon": asset.location.lon},
                    # Omit capacity when unknown so routing applies its default.
                    **({"capacity": int(asset.capacity)} if asset.capacity else {}),
                }
            )
        return depots

    @staticmethod
    def _zone_from_cell(cell: DemandCell) -> dict[str, Any]:
        """Serialise a demand cell into the routing tier's ``zone`` shape.

        The EvacuationRoutingAgent expects ``zone_id``/``cell_id``, a
        ``centroid``/``location`` and a ``population`` (plus optional per-class
        breakdown). We map the cell's at-risk population to evacuation classes
        using its vulnerability profile so the routing priority order
        (mobility_impaired > elderly > children > hospitalised > general) is
        honoured downstream.
        """
        v = cell.vulnerability
        classes: dict[str, int] = {}
        if v.mobility_impaired:
            classes["mobility_impaired"] = int(v.mobility_impaired)
        if v.children:
            classes["children"] = int(v.children)
        if v.hospitalised:
            classes["hospitalised"] = int(v.hospitalised)
        accounted = sum(classes.values())
        general = max(0, int(cell.population_at_risk) - accounted)
        if general:
            classes["general"] = general
        zone: dict[str, Any] = {
            "zone_id": cell.cell_id,
            "cell_id": cell.cell_id,
            "centroid": {"lat": cell.centroid.lat, "lon": cell.centroid.lon},
            "population": int(cell.population_at_risk),
            "probability": cell.probability,
            "horizon_minutes": cell.horizon_minutes,
        }
        if classes:
            zone["classes"] = classes
        return zone

    def _build_demand(self, payload: dict[str, Any]) -> list[DemandCell]:
        """Join prediction risk-cells with the vulnerability map -> DemandCells."""
        cells: list[DemandCell] = []
        for rc in payload.get("risk_cells", []):
            cell_id = str(rc.get("cell_id", ""))
            if not cell_id:
                continue
            # Vulnerability: prefer the explicit map, else any inline profile.
            vuln = self.vulnerability_map.get(cell_id)
            if vuln is None:
                vuln = _vuln(rc.get("vulnerability"))
            cells.append(
                DemandCell(
                    cell_id=cell_id,
                    centroid=_latlon(rc.get("centroid")),
                    population_at_risk=int(rc.get("population_at_risk", 0)),
                    probability=float(rc.get("probability", 0.0)),
                    horizon_minutes=int(rc.get("horizon_minutes", 0)),
                    vulnerability=vuln,
                    needed_asset_types=tuple(rc.get("needed_asset_types", []) or ()),
                )
            )
        return cells

    def _asset_views(self) -> list[AssetView]:
        views: list[AssetView] = []
        for a in self.assets.values():
            if not a.available:
                continue
            views.append(
                AssetView(
                    asset_id=a.asset_id,
                    type=a.type.value if isinstance(a.type, AssetType) else str(a.type),
                    location=a.location,
                    capacity=a.capacity,
                    fuel_pct=a.fuel_pct,
                )
            )
        return views

    def _to_orders(
        self, result, cells: list[DemandCell], penalty: float
    ) -> tuple[list[DeploymentOrder], list[str]]:
        cell_by_id = {c.cell_id: c for c in cells}
        orders: list[DeploymentOrder] = []
        cross_state: list[str] = []
        for asn in result.assignments:
            cell = cell_by_id.get(asn.cell_id)
            order_id = f"DO-{uuid.uuid4().hex[:10]}"
            prio = self._order_priority(asn, cell, penalty)
            reason = self._order_reason(asn, cell)
            orders.append(
                DeploymentOrder(
                    order_id=order_id,
                    asset_id=asn.asset_id,
                    target_cell=asn.cell_id,
                    priority=prio,
                    reason=reason,
                    eta_minutes=round(asn.eta_minutes, 1),
                )
            )
            # Mutual aid beyond the pre-position radius and crossing a state
            # boundary is the autonomous/escalation frontier (PRD Step 7).
            if self._is_cross_state(asn):
                cross_state.append(order_id)
        return orders, cross_state

    def _order_priority(self, asn, cell: DemandCell | None, penalty: float) -> int:
        """1 (most urgent) .. 5. Equity weight + cascade urgency raise priority."""
        score = 3.0
        if cell is not None:
            score -= min(1.5, (cell.equity_weight - 1.0))
            if cell.population_at_risk >= _HIGH_DEMAND_THRESHOLD:
                score -= 0.5
            if cell.probability >= 0.7:
                score -= 0.5
        score -= (penalty - 1.0)  # cascade pressure
        if asn.within_preposition:
            score -= 0.25  # we can act immediately
        return max(1, min(5, int(round(score))))

    def _order_reason(self, asn, cell: DemandCell | None) -> str:
        bits = []
        if asn.within_preposition:
            bits.append(f"pre-positioned (<{int(PREPOSITION_RADIUS_KM)}km) auto-deploy")
        else:
            bits.append(f"mutual-aid reach {asn.distance_km}km")
        if cell is not None:
            bits.append(
                f"covers {asn.covered}/{cell.population_at_risk} at-risk "
                f"(equity x{round(cell.equity_weight, 2)})"
            )
        bits.append(f"ETA {asn.eta_minutes}min")
        return "; ".join(bits)

    def _is_cross_state(self, asn) -> bool:
        """Heuristic: assignment beyond the pre-position radius is treated as
        potential inter-district / inter-state mutual aid that the commander
        must review. Within 50 km is autonomous (PRD Step 7)."""
        return not asn.within_preposition

    def _to_gaps(self, result, cells: list[DemandCell]) -> list[ResourceGap]:
        gaps: list[ResourceGap] = []
        for cell in cells:
            rem = result.shortfall.get(cell.cell_id, 0)
            if rem <= 0:
                continue
            asset_type = (
                cell.needed_asset_types[0]
                if cell.needed_asset_types
                else AssetType.NDRF_TEAM.value
            )
            gaps.append(
                ResourceGap(
                    zone_id=cell.cell_id,
                    asset_type=_asset_type(asset_type),
                    shortfall=int(rem),
                    note=(
                        f"{rem} at-risk uncovered (equity x{round(cell.equity_weight, 2)}); "
                        "recommend mutual-aid request / depot requisition"
                    ),
                )
            )
        return gaps

    # ------------------------------------------------------------------- helpers
    def _prefer_lp(self) -> bool:
        # Allow a settings flag to force the stdlib greedy path (degraded mode).
        if self.settings is not None and getattr(self.settings, "use_kafka", None) is None:
            pass
        return True


# --------------------------------------------------------------------------- coercion
def _as_cascade_failure(obj: Any) -> CascadeFailure:
    if isinstance(obj, CascadeFailure):
        return obj
    if isinstance(obj, dict):
        return CascadeFailure(
            segment_id=str(obj.get("segment_id", "")),
            fails_at_minute=int(obj.get("fails_at_minute", 10_000)),
            reason=str(obj.get("reason", "")),
            viable_until_minute=int(obj.get("viable_until_minute", 10_000)),
        )
    return CascadeFailure(segment_id="", fails_at_minute=10_000, reason="", viable_until_minute=10_000)


def _asset_type(value: Any) -> AssetType:
    if isinstance(value, AssetType):
        return value
    try:
        return AssetType(str(value))
    except ValueError:
        return AssetType.NDRF_TEAM


def _module_from(value: Any, default: Module) -> Module:
    if isinstance(value, Module):
        return value
    if value is None:
        return default
    try:
        return Module(str(value))
    except ValueError:
        return default


def _sample_assets() -> list[Asset]:
    """A small deterministic pre-positioned inventory for dry-runs/tests.

    Coordinates cluster around coastal Odisha/AP (cyclone-prone) so a nearby
    prediction yields within-50km autonomous deployments out of the box.
    """
    return [
        Asset("BOAT-01", AssetType.BOAT, LatLon(20.27, 85.84), capacity=20, fuel_pct=95.0),
        Asset("BOAT-02", AssetType.BOAT, LatLon(20.35, 85.90), capacity=20, fuel_pct=80.0),
        Asset("NDRF-01", AssetType.NDRF_TEAM, LatLon(20.30, 85.82), capacity=200),
        Asset("SDRF-01", AssetType.SDRF_TEAM, LatLon(20.25, 85.88), capacity=150),
        Asset("MED-01", AssetType.MEDICAL_UNIT, LatLon(20.29, 85.83), capacity=60),
        Asset("HELI-01", AssetType.HELICOPTER, LatLon(20.24, 85.81), capacity=12),
        Asset("USAR-01", AssetType.USAR_TEAM, LatLon(20.31, 85.86), capacity=40),
        Asset("FIRE-01", AssetType.FIRE_ENGINE, LatLon(20.28, 85.85), capacity=10),
    ]
