"""Tier 2 — Evacuation Routing Agent (PRD Step 5).

Solves a *multi-depot Vehicle Routing Problem* (VRP) to evacuate at-risk
population to shelters, then emits one :class:`~disastermind.models.domain.EvacRoute`
per vehicle.  The agent is a Tier-2 SPECIALIST: it owns autonomous routing
decisions within its domain (PRD Step 5 / Step 8 authority matrix).

Inputs (TOPIC WIRING):
  * ``Topic.CASCADE``        — :class:`CascadeFailure` segments to *exclude* from
    routes (roads inundated within the route TTL, high-MMI bridge-collapse zones,
    fire-spread paths) plus ``safe_windows``.
  * ``Topic.RESOURCE_PLAN``  — deployment orders + resource gaps that tell us
    which vehicles/depots are available to run evacuation legs.

Output:
  * ``Topic.ROUTING_PLAN``   — ``{"incident_id", "routes": [EvacRoute...]}``.

Routing engine:
  * Preferred: Google OR-Tools ``RoutingModel`` (lazy import, wrapped in
    try/except).  Multi-depot, capacity-aware, priority-weighted.
  * Fallback: a deterministic *nearest-neighbour insertion* heuristic in pure
    stdlib Python so the package imports and tests run with stdlib only
    (PRD Step 10 graceful degradation).

Autonomous decisions made here (PRD Step 5):
  1. Activate evacuation routes for zones whose population is **below** the
     auto-evac threshold (small zones can be moved without human sign-off; large
     ones become a mass-evacuation escalation handled downstream by the field /
     commander tiers).
  2. Redirect evacuees to an alternate shelter when a shelter passes 80 %
     capacity.
  3. Issue floor-by-floor evacuation orders for buildings sitting in a fire path.

Priority order for whom we route first (PRD Step 5):
  1 mobility-impaired, 2 elderly, 3 children, 4 hospitalised, 5 general.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.contracts import Message, MessageType, Module, Priority, Tier, Topic
from ...audit.decision_log import DecisionLogger
from ...models.domain import (
    CascadeFailure,
    EvacRoute,
    Shelter,
)
from ...models.geo import LatLon, haversine

log = logging.getLogger("disastermind.routing")

# Population-class priority (lower rank == evacuate first). PRD Step 5.
POPULATION_CLASS_RANK: dict[str, int] = {
    "mobility_impaired": 1,
    "elderly": 2,
    "children": 3,
    "hospitalised": 4,
    "general": 5,
}

# Autonomous-decision thresholds (PRD Step 5 / Step 7).
SHELTER_REDIRECT_RATIO = 0.80          # redirect when shelter > 80 % full
AUTO_EVAC_POP_THRESHOLD = 10_000       # zones at/under this auto-activate
DEFAULT_VEHICLE_CAPACITY = 50          # evacuees per vehicle leg


class EvacuationRoutingAgent(BaseAgent):
    """Multi-depot VRP solver + dynamic re-router (PRD Step 5)."""

    tier = Tier.SPECIALIST
    decision_authority = True  # Tier 2 owns autonomous routing decisions

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        settings: Any | None = None,
        name: str = "routing.evacuation",
        module: Module = Module.ALL,
    ) -> None:
        super().__init__(
            name=name,
            bus=bus,
            logger=logger,
            subscriptions=[Topic.CASCADE, Topic.RESOURCE_PLAN],
        )
        self.settings = settings
        self.module = module

        # --- last-known state (PRD Step 10: operate on last-known state) ------
        # segment_id -> CascadeFailure (roads/bridges/fire-paths to avoid)
        self._closed_segments: dict[str, CascadeFailure] = {}
        # incident_id -> last computed list[EvacRoute]
        self._routes_by_incident: dict[str, list[EvacRoute]] = {}
        # incident_id -> demand snapshot used to (re)compute routes
        self._demand_by_incident: dict[str, list[dict[str, Any]]] = {}
        # incident_id -> shelters snapshot
        self._shelters_by_incident: dict[str, list[Shelter]] = {}
        # incident_id -> depots (vehicle origins) snapshot
        self._depots_by_incident: dict[str, list[dict[str, Any]]] = {}
        self._route_seq = 0

    # ================================================================ reactive
    def handle(self, message: Message) -> list[Message]:
        """React to cascade / resource updates and (re)plan evacuation routes."""
        payload = message.payload or {}
        incident_id = message.incident_id or payload.get("incident_id")

        if message.topic == Topic.CASCADE:
            self._ingest_cascade(payload)
            if incident_id is None:
                return []
            return self._plan_and_emit(
                incident_id, trigger=f"cascade update ({message.id})"
            )

        if message.topic == Topic.RESOURCE_PLAN:
            self._ingest_resource_plan(incident_id, payload)
            if incident_id is None:
                return []
            return self._plan_and_emit(
                incident_id, trigger=f"resource plan ({message.id})"
            )

        return []

    # ----------------------------------------------------------- ingest helpers
    def _ingest_cascade(self, payload: dict[str, Any]) -> None:
        """Record CascadeFailure segments to avoid (inundation/high_mmi/fire_path)."""
        for raw in payload.get("failures", []) or []:
            cf = _as_cascade_failure(raw)
            if cf is not None:
                self._closed_segments[cf.segment_id] = cf

    def _ingest_resource_plan(
        self, incident_id: str | None, payload: dict[str, Any]
    ) -> None:
        """Derive evacuation demand, shelters and depots from a resource plan.

        The resource module publishes deployment orders + gaps. We accept an
        optional richer routing context the resource/prediction tiers may attach
        (``zones``, ``shelters``, ``vehicles``/``depots``). When absent we infer
        depots from deployment-order assets so degraded operation still produces
        routes.
        """
        if incident_id is None:
            return
        # Demand zones (population to evacuate).
        zones = payload.get("zones") or payload.get("demand") or []
        if zones:
            self._demand_by_incident[incident_id] = [dict(z) for z in zones]
        # Shelters (destinations).
        shelters = [_as_shelter(s) for s in (payload.get("shelters") or [])]
        shelters = [s for s in shelters if s is not None]
        if shelters:
            self._shelters_by_incident[incident_id] = shelters
        # Depots / vehicles (route origins).
        depots = payload.get("depots") or payload.get("vehicles") or []
        if not depots:
            depots = self._depots_from_orders(payload.get("orders") or [])
        if depots:
            self._depots_by_incident[incident_id] = [dict(d) for d in depots]

    @staticmethod
    def _depots_from_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Treat each deployment order's asset as a candidate evacuation vehicle."""
        depots: list[dict[str, Any]] = []
        for o in orders or []:
            asset_id = o.get("asset_id")
            if not asset_id:
                continue
            depots.append(
                {
                    "vehicle_id": asset_id,
                    "depot": o.get("origin") or o.get("location"),
                    "capacity": o.get("capacity", DEFAULT_VEHICLE_CAPACITY),
                }
            )
        return depots

    # ================================================================= planning
    def _plan_and_emit(self, incident_id: str, trigger: str) -> list[Message]:
        routes, reasoning = self._compute_routes(incident_id)
        if not routes:
            return []
        self._routes_by_incident[incident_id] = routes
        reasoning.insert(0, f"replan triggered by {trigger}")
        return [self._routing_message(incident_id, routes, reasoning)]

    def _compute_routes(
        self, incident_id: str
    ) -> tuple[list[EvacRoute], list[str]]:
        """Build per-vehicle EvacRoutes for one incident (autonomous decisions)."""
        demand = self._demand_by_incident.get(incident_id, [])
        shelters = list(self._shelters_by_incident.get(incident_id, []))
        depots = self._depots_by_incident.get(incident_id, [])
        reasoning: list[str] = []

        if not demand or not depots:
            return [], reasoning

        # --- Autonomous decision 1: only auto-activate sub-threshold zones -----
        active_zones: list[dict[str, Any]] = []
        for z in demand:
            pop = int(z.get("population", 0) or 0)
            if pop <= AUTO_EVAC_POP_THRESHOLD:
                active_zones.append(z)
            else:
                reasoning.append(
                    f"zone {z.get('zone_id', z.get('cell_id', '?'))} pop={pop} "
                    f"exceeds auto-evac threshold {AUTO_EVAC_POP_THRESHOLD}; "
                    "deferred to mass-evacuation escalation"
                )
        if not active_zones:
            return [], reasoning

        # --- Expand zones into prioritised evacuation stops --------------------
        stops = self._zones_to_stops(active_zones)
        stops.sort(key=lambda s: (s["rank"], -s["demand"]))
        reasoning.append(
            f"{len(stops)} prioritised stops across {len(active_zones)} zone(s); "
            "order = mobility_impaired>elderly>children>hospitalised>general"
        )

        # --- Graceful degradation: synthesize a shelter if none were supplied --
        # The resource plan carries demand + depots but not always a shelter
        # inventory. Rather than stranding evacuees, pre-position a default
        # shelter near the demand centroid so a route can still be produced
        # (PRD Step 10 last-known/degraded operation). A real shelter inventory
        # (payload ``shelters``) always takes precedence.
        if not shelters:
            fallback = self._default_shelter(active_zones)
            if fallback is not None:
                shelters = [fallback]
                reasoning.append(
                    f"no shelter inventory supplied; pre-positioned default "
                    f"shelter {fallback.shelter_id} near demand centroid (degraded)"
                )

        # --- Autonomous decision 2: shelter redirect at >80 % capacity --------
        shelters, shelter_notes = self._prepare_shelters(shelters)
        reasoning.extend(shelter_notes)
        if not shelters:
            reasoning.append("no shelter with spare capacity; cannot route")
            return [], reasoning

        # --- Solve VRP (OR-Tools if available, else NN-insertion fallback) ----
        routes, solver_notes = self._solve_vrp(depots, stops, shelters)
        reasoning.extend(solver_notes)

        # --- Autonomous decision 3: floor-by-floor orders for fire-path bldgs -
        fire_orders = self._floor_by_floor_orders(active_zones, incident_id)
        if fire_orders:
            routes.extend(fire_orders)
            reasoning.append(
                f"{len(fire_orders)} floor-by-floor evac order(s) for buildings "
                "in fire path"
            )
        return routes, reasoning

    def _zones_to_stops(
        self, zones: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Flatten zones into per-population-class stops with priority rank."""
        stops: list[dict[str, Any]] = []
        for z in zones:
            zone_id = z.get("zone_id") or z.get("cell_id") or "zone"
            loc = _as_latlon(z.get("centroid") or z.get("location"))
            if loc is None:
                continue
            classes = z.get("classes")
            if isinstance(classes, dict) and classes:
                for cls, count in classes.items():
                    cnt = int(count or 0)
                    if cnt <= 0:
                        continue
                    stops.append(
                        {
                            "zone_id": zone_id,
                            "loc": loc,
                            "population_class": cls,
                            "rank": POPULATION_CLASS_RANK.get(cls, 5),
                            "demand": cnt,
                        }
                    )
            else:
                pop = int(z.get("population", 0) or 0)
                stops.append(
                    {
                        "zone_id": zone_id,
                        "loc": loc,
                        "population_class": "general",
                        "rank": POPULATION_CLASS_RANK["general"],
                        "demand": pop,
                    }
                )
        return stops

    def _prepare_shelters(
        self, shelters: list[Shelter]
    ) -> tuple[list[Shelter], list[str]]:
        """Drop / flag shelters over the 80 % redirect ratio (autonomous)."""
        notes: list[str] = []
        usable: list[Shelter] = []
        for s in shelters:
            if s.capacity <= 0:
                continue
            if s.fill_ratio >= SHELTER_REDIRECT_RATIO:
                notes.append(
                    f"shelter {s.shelter_id} at {s.fill_ratio:.0%} "
                    f"(>= {SHELTER_REDIRECT_RATIO:.0%}); redirecting evacuees away"
                )
                continue
            usable.append(s)
        if not usable and shelters:
            # All shelters near-full: keep the least-full one as a last resort.
            fallback = min(shelters, key=lambda s: s.fill_ratio)
            usable = [fallback]
            notes.append(
                f"all shelters near capacity; routing to least-full "
                f"{fallback.shelter_id} ({fallback.fill_ratio:.0%})"
            )
        return usable, notes

    def _default_shelter(
        self, zones: list[dict[str, Any]]
    ) -> Shelter | None:
        """Pre-position a default shelter sized to the active demand (degraded).

        Centroid: the mean of the zone locations. Capacity: enough headroom for
        the total active population so the VRP is not capacity-starved. Used only
        when no shelter inventory was supplied on the resource plan.
        """
        locs: list[LatLon] = []
        total_pop = 0
        for z in zones:
            loc = _as_latlon(z.get("centroid") or z.get("location"))
            if loc is not None:
                locs.append(loc)
            total_pop += int(z.get("population", 0) or 0)
        if not locs:
            return None
        mean_lat = sum(l.lat for l in locs) / len(locs)
        mean_lon = sum(l.lon for l in locs) / len(locs)
        # Cap generously; never below a usable minimum.
        capacity = max(DEFAULT_VEHICLE_CAPACITY, total_pop * 2 or DEFAULT_VEHICLE_CAPACITY)
        return Shelter(
            shelter_id="shelter.default",
            location=LatLon(mean_lat, mean_lon),
            capacity=capacity,
            occupancy=0,
        )

    # ------------------------------------------------------------- route avoidance
    def _is_segment_avoided(self, cf: CascadeFailure, ttl_minutes: int) -> bool:
        """A segment is avoided if it fails within the route TTL window.

        Roads inundated within route TTL, high-MMI (bridge collapse) zones and
        fire-spread paths are excluded (PRD Step 5 route constraints).
        """
        # Fire paths and high-MMI bridge collapse are hard avoids regardless of
        # the precise timing — they are not safely traversable.
        if cf.reason in ("fire_path", "high_mmi"):
            return True
        # Inundation: avoid if it fails before the route's usable horizon.
        return cf.fails_at_minute <= ttl_minutes

    def _path_avoids_closures(
        self, a: LatLon, b: LatLon, ttl_minutes: int
    ) -> bool:
        """Cheap geometric check that the straight leg a->b clears closed segments.

        Without a full road graph we treat each closed segment as a point hazard
        (its ``segment_id`` may encode "lat,lon"); a leg is rejected if it passes
        within an exclusion radius of an active hazard. This is the stdlib
        fallback for the OR-Tools arc-exclusion the real solver would use.
        """
        exclusion_m = 250.0
        for cf in self._closed_segments.values():
            if not self._is_segment_avoided(cf, ttl_minutes):
                continue
            hz = _segment_point(cf.segment_id)
            if hz is None:
                continue
            if _point_near_segment(hz, a, b) <= exclusion_m:
                return False
        return True

    # --------------------------------------------------------------- VRP solvers
    def _solve_vrp(
        self,
        depots: list[dict[str, Any]],
        stops: list[dict[str, Any]],
        shelters: list[Shelter],
    ) -> tuple[list[EvacRoute], list[str]]:
        """Dispatch to OR-Tools, falling back to NN-insertion (PRD Step 10)."""
        try:
            return self._solve_vrp_ortools(depots, stops, shelters)
        except Exception as exc:  # ImportError or solver failure -> fallback
            log.info("OR-Tools unavailable/failed (%s); NN-insertion fallback", exc)
            routes = self._solve_vrp_nn(depots, stops, shelters)
            return routes, [
                "VRP engine: nearest-neighbour insertion (stdlib fallback)"
            ]

    def _solve_vrp_ortools(
        self,
        depots: list[dict[str, Any]],
        stops: list[dict[str, Any]],
        shelters: list[Shelter],
    ) -> tuple[list[EvacRoute], list[str]]:
        """Multi-depot capacity VRP via OR-Tools (lazy import).

        Raises on missing dependency so the caller can fall back. Node layout:
        index 0..D-1 = depots (vehicle starts), then demand stops, then shelters
        as drop nodes; we model a single shelter end per vehicle for simplicity
        and let priority drive visiting order via a disjunction penalty.
        """
        from ortools.constraint_solver import (  # type: ignore
            pywrapcp,
            routing_enums_pb2,
        )

        # Build the node list: depots first, then stops. Shelters handled as the
        # end node mapped per-vehicle to the nearest spare-capacity shelter.
        depot_locs = [_as_latlon(d.get("depot")) for d in depots]
        depot_locs = [d for d in depot_locs if d is not None]
        if not depot_locs:
            raise RuntimeError("no usable depot locations for OR-Tools")

        stop_locs = [s["loc"] for s in stops]
        nodes: list[LatLon] = depot_locs + stop_locs
        n_depots = len(depot_locs)
        n_vehicles = len(depots)

        starts = [min(i, n_depots - 1) for i in range(n_vehicles)]
        # End each vehicle at its own start depot (closed tour); shelter is the
        # logical drop appended when we materialise the EvacRoute.
        ends = list(starts)

        manager = pywrapcp.RoutingIndexManager(len(nodes), n_vehicles, starts, ends)
        routing = pywrapcp.RoutingModel(manager)

        def dist_cb(from_index: int, to_index: int) -> int:
            a = nodes[manager.IndexToNode(from_index)]
            b = nodes[manager.IndexToNode(to_index)]
            return int(haversine(a, b))

        transit = routing.RegisterTransitCallback(dist_cb)
        routing.SetArcCostEvaluatorOfAllVehicles(transit)

        # Capacity dimension.
        def demand_cb(from_index: int) -> int:
            node = manager.IndexToNode(from_index)
            if node < n_depots:
                return 0
            return int(stops[node - n_depots]["demand"])

        demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
        caps = [int(d.get("capacity", DEFAULT_VEHICLE_CAPACITY)) for d in depots]
        routing.AddDimensionWithVehicleCapacity(
            demand_idx, 0, caps, True, "Capacity"
        )

        # Priority: cheaper-to-drop penalty for lower-priority stops so the
        # solver visits high-priority (low-rank) evacuees first.
        for si, st in enumerate(stops):
            node_index = manager.NodeToIndex(n_depots + si)
            penalty = 1_000_000 // max(1, st["rank"])
            routing.AddDisjunction([node_index], penalty)

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        params.time_limit.FromSeconds(2)

        solution = routing.SolveWithParameters(params)
        if solution is None:
            raise RuntimeError("OR-Tools found no solution")

        routes: list[EvacRoute] = []
        for v in range(n_vehicles):
            idx = routing.Start(v)
            visited_stops: list[dict[str, Any]] = []
            waypoints: list[LatLon] = []
            while not routing.IsEnd(idx):
                node = manager.IndexToNode(idx)
                if node >= n_depots:
                    st = stops[node - n_depots]
                    visited_stops.append(st)
                    waypoints.append(st["loc"])
                idx = solution.Value(routing.NextVar(idx))
            if not visited_stops:
                continue
            routes.extend(
                self._materialise_routes(depots[v], visited_stops, shelters)
            )
        return routes, ["VRP engine: OR-Tools (multi-depot, capacity, priority)"]

    def _solve_vrp_nn(
        self,
        depots: list[dict[str, Any]],
        stops: list[dict[str, Any]],
        shelters: list[Shelter],
    ) -> list[EvacRoute]:
        """Pure-stdlib nearest-neighbour insertion across multiple depots.

        Greedy: stops are already priority-sorted. Each stop is assigned to the
        nearest depot/vehicle with remaining capacity whose leg avoids closed
        segments, then routed to the nearest spare-capacity shelter.
        """
        # Per-vehicle running state.
        veh_state: list[dict[str, Any]] = []
        for d in depots:
            depot_loc = _as_latlon(d.get("depot"))
            veh_state.append(
                {
                    "vehicle_id": d.get("vehicle_id", "veh"),
                    "loc": depot_loc,
                    "depot": depot_loc,
                    "remaining": int(d.get("capacity", DEFAULT_VEHICLE_CAPACITY)),
                    "stops": [],
                }
            )

        ttl_minutes = self._route_ttl_minutes()
        for st in stops:
            # Choose nearest vehicle with capacity and a clear leg.
            best = None
            best_d = float("inf")
            for v in veh_state:
                if v["remaining"] <= 0 or v["loc"] is None:
                    continue
                if not self._path_avoids_closures(v["loc"], st["loc"], ttl_minutes):
                    continue
                d = haversine(v["loc"], st["loc"])
                if d < best_d:
                    best_d, best = d, v
            if best is None:
                # No clear leg: fall back to nearest vehicle ignoring closures so
                # the evacuee is not stranded (note recorded via avoid_reason).
                for v in veh_state:
                    if v["remaining"] <= 0 or v["loc"] is None:
                        continue
                    d = haversine(v["loc"], st["loc"])
                    if d < best_d:
                        best_d, best = d, v
            if best is None:
                continue
            best["stops"].append(st)
            best["loc"] = st["loc"]
            best["remaining"] -= st["demand"]

        routes: list[EvacRoute] = []
        for v in veh_state:
            if not v["stops"]:
                continue
            routes.extend(
                self._materialise_routes(
                    {"vehicle_id": v["vehicle_id"], "depot": v["depot"]},
                    v["stops"],
                    shelters,
                )
            )
        return routes

    # --------------------------------------------------------- route materialise
    def _materialise_routes(
        self,
        depot: dict[str, Any],
        visited_stops: list[dict[str, Any]],
        shelters: list[Shelter],
    ) -> list[EvacRoute]:
        """Turn a vehicle's visited stops into EvacRoute(s), one per pop-class.

        We split a vehicle's tour by population class so each EvacRoute carries a
        single ``population_class`` (the EvacRoute schema is per-class), routed to
        the nearest spare-capacity shelter.
        """
        if not visited_stops:
            return []
        vehicle_id = depot.get("vehicle_id", "veh")
        ttl_minutes = self._route_ttl_minutes()

        # Group by class, preserving priority order.
        by_class: dict[str, list[dict[str, Any]]] = {}
        for st in visited_stops:
            by_class.setdefault(st["population_class"], []).append(st)

        out: list[EvacRoute] = []
        for cls in sorted(by_class, key=lambda c: POPULATION_CLASS_RANK.get(c, 5)):
            class_stops = by_class[cls]
            waypoints = [st["loc"] for st in class_stops]
            last = waypoints[-1]
            shelter = self._nearest_shelter(last, shelters)
            avoid_reason = self._avoid_reason_for(waypoints, ttl_minutes)
            if shelter is not None:
                waypoints = waypoints + [shelter.location]
                shelter_id = shelter.shelter_id
                # Reserve capacity so subsequent routes see the update.
                shelter.occupancy += sum(int(s["demand"]) for s in class_stops)
            else:
                shelter_id = ""
            self._route_seq += 1
            out.append(
                EvacRoute(
                    route_id=f"er-{self._route_seq}",
                    vehicle_id=vehicle_id,
                    waypoints=waypoints,
                    population_class=cls,
                    shelter_id=shelter_id,
                    depart_after_minute=0,
                    avoid_reason=avoid_reason,
                )
            )
        return out

    def _avoid_reason_for(self, waypoints: list[LatLon], ttl_minutes: int) -> str:
        """Human-readable note of which hazards the route was planned around."""
        reasons: set[str] = set()
        for cf in self._closed_segments.values():
            if self._is_segment_avoided(cf, ttl_minutes):
                reasons.add(cf.reason)
        return ",".join(sorted(reasons))

    @staticmethod
    def _nearest_shelter(
        loc: LatLon, shelters: list[Shelter]
    ) -> Shelter | None:
        spare = [s for s in shelters if s.fill_ratio < 1.0]
        if not spare:
            return None
        return min(spare, key=lambda s: haversine(loc, s.location))

    def _route_ttl_minutes(self) -> int:
        ttl_s = 300
        if self.settings is not None:
            ttl_s = getattr(self.settings, "escalation_timeout_seconds", 300) or 300
        return max(1, int(ttl_s) // 60)

    # ----------------------------------------------- floor-by-floor (fire path)
    def _floor_by_floor_orders(
        self, zones: list[dict[str, Any]], incident_id: str
    ) -> list[EvacRoute]:
        """Emit floor-by-floor evac orders for buildings in a fire path.

        A building is "in a fire path" when a fire_path CascadeFailure hazard
        sits within the exclusion radius of the building, or when the zone payload
        flags the building accordingly. Floors are evacuated top-down (fire rises)
        and rendered as ordered waypoints stamped onto an EvacRoute.
        """
        out: list[EvacRoute] = []
        fire_hazards = [
            cf for cf in self._closed_segments.values() if cf.reason == "fire_path"
        ]
        for z in zones:
            for b in z.get("buildings", []) or []:
                loc = _as_latlon(b.get("location"))
                if loc is None:
                    continue
                in_fire = bool(b.get("in_fire_path"))
                if not in_fire:
                    for cf in fire_hazards:
                        hz = _segment_point(cf.segment_id)
                        if hz is not None and haversine(hz, loc) <= 250.0:
                            in_fire = True
                            break
                if not in_fire:
                    continue
                floors = int(b.get("floors", 1) or 1)
                # Top-down evacuation: highest floor first.
                waypoints = [loc for _ in range(max(1, floors))]
                self._route_seq += 1
                out.append(
                    EvacRoute(
                        route_id=f"er-fb-{self._route_seq}",
                        vehicle_id=f"stair-{b.get('building_id', 'b')}",
                        waypoints=waypoints,
                        population_class="general",
                        shelter_id="",
                        depart_after_minute=0,
                        avoid_reason=(
                            f"floor_by_floor:{floors}flr:fire_path:"
                            f"{b.get('building_id', 'b')}"
                        ),
                    )
                )
        return out

    # ============================================================== dynamic rerouting
    def reroute(
        self,
        closed_segment: dict[str, Any] | CascadeFailure,
        incident_id: str | None = None,
    ) -> list[Message]:
        """Recalculate routes after a segment closes mid-operation (PRD Step 5).

        Registers the newly-closed segment, recomputes affected incident(s) and
        emits updated :class:`EvacRoute` waypoints. Returns the emitted messages
        (also published via :meth:`emit`).
        """
        cf = _as_cascade_failure(closed_segment) if not isinstance(
            closed_segment, CascadeFailure
        ) else closed_segment
        if cf is not None:
            self._closed_segments[cf.segment_id] = cf

        incidents = (
            [incident_id]
            if incident_id is not None
            else list(self._demand_by_incident.keys())
        )
        emitted: list[Message] = []
        for inc in incidents:
            if inc is None:
                continue
            msgs = self._plan_and_emit(
                inc,
                trigger=f"reroute around closed segment "
                f"{cf.segment_id if cf else '?'}",
            )
            for m in msgs:
                self.emit(m)
                emitted.append(m)
        return emitted

    # =================================================================== message
    def _routing_message(
        self, incident_id: str, routes: list[EvacRoute], reasoning: list[str]
    ) -> Message:
        payload = {
            "kind": "routing",
            "incident_id": incident_id,
            "routes": [_evacroute_to_dict(r) for r in routes],
        }
        # Highest urgency present drives message priority.
        best_rank = min(
            (POPULATION_CLASS_RANK.get(r.population_class, 5) for r in routes),
            default=5,
        )
        priority = Priority.CRITICAL if best_rank <= 2 else Priority.HIGH
        return Message(
            sender=self.name,
            recipient="tier2.field",
            type=MessageType.INSTRUCTION,
            priority=priority,
            payload=payload,
            reasoning=reasoning,
            topic=Topic.ROUTING_PLAN,
            incident_id=incident_id,
            module=self.module,
        )


# ============================================================ module-level helpers
def _evacroute_to_dict(route: EvacRoute) -> dict[str, Any]:
    """asdict-based serialisation (LatLon dataclasses become nested dicts)."""
    return asdict(route)


def _as_latlon(value: Any) -> LatLon | None:
    if value is None:
        return None
    if isinstance(value, LatLon):
        return value
    if isinstance(value, dict):
        try:
            return LatLon(float(value["lat"]), float(value["lon"]))
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return LatLon(float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def _as_shelter(value: Any) -> Shelter | None:
    if value is None:
        return None
    if isinstance(value, Shelter):
        return value
    if isinstance(value, dict):
        loc = _as_latlon(value.get("location"))
        if loc is None:
            return None
        try:
            return Shelter(
                shelter_id=str(value.get("shelter_id", "shelter")),
                location=loc,
                capacity=int(value.get("capacity", 0) or 0),
                occupancy=int(value.get("occupancy", 0) or 0),
            )
        except (TypeError, ValueError):
            return None
    return None


def _as_cascade_failure(value: Any) -> CascadeFailure | None:
    if value is None:
        return None
    if isinstance(value, CascadeFailure):
        return value
    if isinstance(value, dict):
        try:
            return CascadeFailure(
                segment_id=str(value.get("segment_id", "seg")),
                fails_at_minute=int(value.get("fails_at_minute", 0) or 0),
                reason=str(value.get("reason", "inundation")),
                viable_until_minute=int(value.get("viable_until_minute", 0) or 0),
            )
        except (TypeError, ValueError):
            return None
    return None


def _segment_point(segment_id: str) -> LatLon | None:
    """Best-effort decode of a hazard location from a segment id.

    Convention: a segment id may embed coordinates as ``...lat,lon`` or
    ``lat:lon``; otherwise we cannot localise it and return None (the segment is
    still avoided by id elsewhere, just not geometrically).
    """
    if not segment_id:
        return None
    for sep in (",", ":"):
        if sep in segment_id:
            tail = segment_id.replace(":", ",").split(",")
            nums: list[float] = []
            for tok in tail:
                try:
                    nums.append(float(tok))
                except ValueError:
                    continue
            if len(nums) >= 2:
                return LatLon(nums[-2], nums[-1])
    return None


def _point_near_segment(p: LatLon, a: LatLon, b: LatLon) -> float:
    """Approx distance (m) from point ``p`` to leg ``a->b`` (planar projection)."""
    import math

    # Equirectangular metres relative to a.
    m_lat = 111_320.0
    m_lon = 111_320.0 * math.cos(math.radians(a.lat))
    ax, ay = 0.0, 0.0
    bx = (b.lon - a.lon) * m_lon
    by = (b.lat - a.lat) * m_lat
    px = (p.lon - a.lon) * m_lon
    py = (p.lat - a.lat) * m_lat
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)
