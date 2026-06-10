"""Validate the evacuation plan on REAL infrastructure (Cyclone Fani zone, Puri).

The hindcasts proved the system *activates and produces a plan* in time. This goes
one step deeper and asks: would that plan be **feasible on the real ground** —
real roads, real shelters? It loads the committed OpenStreetMap road network and
real shelter buildings of Puri (the Fani landfall/evacuation zone), builds the
real road graph, and routes coastal at-risk junctions to real shelters using the
system's own ``roadnet`` shortest-path engine.

It measures three real things:
  1. **Feasibility / coverage** — what fraction of at-risk coastal junctions can
     actually reach a real shelter over the real road network (vs. a route that
     only exists as a straight line on a map).
  2. **Road-vs-straight-line detour** — how much the real road distance exceeds
     the straight-line distance the naive allocator originally assumed. This is
     the concrete cost of *not* being road-aware.
  3. **Evacuation time within the lead window** — at a realistic evacuation
     speed, do routes complete inside the multi-day pre-landfall lead time.

Honest limits (stated in the report): OSM under-tags shelters in coastal India,
so the destination set is sparse and these are *candidate* real shelters, not the
exact historical Fani shelter assignments (those logs are not public). The road
network and the named buildings (Jagannath Temple, Sanskrit College, ...) are
real OSM data.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from ..models.geo import LatLon, haversine
from ..roadnet import NoRouteError, road_distance
from ..roadnet.graph import RoadGraph

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "puri_osm.json")


@dataclass
class EvacValidation:
    place: str
    source: str
    junctions: int
    road_ways: int
    shelters: int
    named_shelters: list[str]
    at_risk_sampled: int
    reached: int
    coverage_pct: float
    road_km_mean: float
    straight_km_mean: float
    detour_ratio_mean: float
    detour_ratio_p90: float
    evac_speed_kmh: float
    max_evac_minutes: float
    within_lead_hours: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def build_graph(fixture: dict) -> RoadGraph:
    """Build a real road graph from the committed OSM ways (one edge per segment)."""
    g = RoadGraph()
    for way in fixture["roads"]:
        pts = way["pts"]
        name = way.get("name") or None
        for i in range(len(pts) - 1):
            g.add_edge(
                LatLon(pts[i][0], pts[i][1]),
                LatLon(pts[i + 1][0], pts[i + 1][1]),
                name=name,
            )
    return g


def load_puri_osm(path: str = FIXTURE) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _percentile(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, int(q * (len(s) - 1)))
    return s[idx]


def validate_evacuation(
    fixture: dict | None = None,
    *,
    sample_size: int = 60,
    coastal_band_deg: float = 0.02,
    evac_speed_kmh: float = 15.0,
    lead_hours: float = 24.0,
) -> EvacValidation:
    """Route real coastal at-risk junctions to real shelters on the real network."""
    fx = fixture or load_puri_osm()
    g = build_graph(fx)
    nodes = list(g.nodes.values())

    shelters = [LatLon(s["lat"], s["lon"]) for s in fx["shelters"]]
    named = [s["name"] for s in fx["shelters"] if s.get("name")]
    # snap each real shelter to its nearest real road junction (shelters sit off-road)
    snapped = [min(nodes, key=lambda n: haversine(sh, n)) for sh in shelters]

    # at-risk origins: the southern coastal band (Puri beach is to the south,
    # most exposed to storm surge) — sampled evenly for a tractable, fair set.
    south = min(n.lat for n in nodes)
    coastal = [n for n in nodes if n.lat < south + coastal_band_deg]
    coastal.sort(key=lambda n: (n.lat, n.lon))
    step = max(1, len(coastal) // sample_size)
    origins = coastal[::step][:sample_size]

    road_km: list[float] = []
    straight_km: list[float] = []
    ratios: list[float] = []
    reached = 0
    for o in origins:
        # try shelters in straight-line order; first reachable on real roads wins
        order = sorted(range(len(snapped)), key=lambda i: haversine(o, snapped[i]))
        for i in order:
            try:
                d = road_distance(g, o, snapped[i])
            except NoRouteError:
                continue
            sl = haversine(o, snapped[i])
            road_km.append(d / 1000.0)
            straight_km.append(sl / 1000.0)
            ratios.append(d / max(1.0, sl))
            reached += 1
            break

    n = len(origins)
    cov = 100.0 * reached / n if n else 0.0
    max_evac_min = (max(road_km) / evac_speed_kmh * 60.0) if road_km else 0.0
    notes = [
        f"Real road network: {len(g.nodes)} junctions from {len(fx['roads'])} OSM ways.",
        f"{len(shelters)} real shelter buildings tagged in OSM (sparse — coastal India "
        "under-tags shelters; the real Fani evacuation used the much larger OSDMA "
        "multipurpose-cyclone-shelter network). These are candidate real shelters, "
        "not the historical Fani assignments (those logs are not public).",
        f"Detour: real road distance averages {sum(ratios) / len(ratios):.2f}x the "
        "straight-line distance the naive allocator assumed — the concrete cost of "
        "not being road-aware." if ratios else "no routes computed",
    ]
    return EvacValidation(
        place=fx.get("place", "Puri, Odisha"),
        source=fx.get("source", "OpenStreetMap"),
        junctions=len(g.nodes),
        road_ways=len(fx["roads"]),
        shelters=len(shelters),
        named_shelters=named[:8],
        at_risk_sampled=n,
        reached=reached,
        coverage_pct=round(cov, 1),
        road_km_mean=round(sum(road_km) / len(road_km), 3) if road_km else 0.0,
        straight_km_mean=round(sum(straight_km) / len(straight_km), 3) if straight_km else 0.0,
        detour_ratio_mean=round(sum(ratios) / len(ratios), 3) if ratios else 0.0,
        detour_ratio_p90=round(_percentile(ratios, 0.9), 3),
        evac_speed_kmh=evac_speed_kmh,
        max_evac_minutes=round(max_evac_min, 1),
        within_lead_hours=lead_hours,
        notes=notes,
    )


def to_markdown(v: EvacValidation) -> str:
    within = "✅" if v.max_evac_minutes <= v.within_lead_hours * 60 else "❌"
    return "\n".join(
        [
            "# Evacuation-Plan Feasibility on REAL Infrastructure — Puri (Cyclone Fani zone)",
            "",
            f"_Source: {v.source}. Real road network + real shelter buildings of "
            f"{v.place}._",
            "",
            "## Real infrastructure",
            f"- **Road network:** {v.junctions:,} junctions from {v.road_ways:,} OSM road ways",
            f"- **Shelters:** {v.shelters} real tagged buildings "
            f"(e.g. {', '.join(v.named_shelters[:4])})",
            "",
            "## The system's routing on the real ground",
            f"- **Coverage:** {v.reached}/{v.at_risk_sampled} sampled coastal at-risk "
            f"junctions reach a real shelter over the real road network "
            f"(**{v.coverage_pct}%** feasible)",
            f"- **Road vs straight-line:** real road distance is **{v.detour_ratio_mean}x** "
            f"the straight-line distance on average (p90 {v.detour_ratio_p90}x) — the "
            "concrete cost the naive allocator's straight-line assumption ignored",
            f"- **Evacuation time:** longest route {v.max_evac_minutes:.0f} min at "
            f"{v.evac_speed_kmh:.0f} km/h — {within} within the {v.within_lead_hours:.0f} h "
            "pre-landfall lead window",
            "",
            "## Honest limits",
            *[f"- {note}" for note in v.notes],
            "- This validates route *feasibility on real infrastructure*, not the "
            "quality of the plan against the real Fani evacuation (shelter occupancy, "
            "exact routes, and capacities from 2019 are not public).",
        ]
    )
