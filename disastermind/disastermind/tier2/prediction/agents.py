"""Tier 2 — Prediction & Assessment agents (PRD Step 3).

Three domain specialists translate raw feeds / IoT telemetry into a common
:class:`~disastermind.models.domain.RiskCell` / ``BuildingImpact`` / ``FireFront``
risk payload published on :data:`~disastermind.core.contracts.Topic.PREDICTION`:

  (A) :class:`CyclonePredictionAgent`
        Module A (cyclone / flood). PRD Step 3 Module A: per-100m grid-cell
        inundation probability at horizons T+6/12/24/48h with population-at-risk.
        Production interface = XGBoost (tabular drivers) + U-Net CNN (spatial
        inundation raster) ENSEMBLE; stdlib FALLBACK = a deterministic
        rainfall/surge/elevation heuristic.

  (B) :class:`EarthquakeImpactAgent`
        Module B (earthquake). PRD Step 3 Module B: HAZUS-style fragility
        collapse probability per building (kutcha / pucca / RCC) + Poisson
        casualty model -> ``BuildingImpact`` list and rescue-priority zones.
        Fallback = ShakeMap MMI -> fragility heuristic.

  (C) :class:`FireSpreadAgent`
        Module C (urban fire / collapse). PRD Step 3 Module C: cellular-automata
        fire-perimeter projection at T+15/30/60min -> ``FireFront`` list and the
        critical infrastructure each front threatens.

Every agent is a Tier 2 SPECIALIST: it makes autonomous predictions, subscribes
to :data:`Topic.RAW_FEED` (+ :data:`Topic.IOT_TELEMETRY`), publishes
:data:`Topic.PREDICTION`, and records a SHAP-style feature attribution through
``logger.log_prediction`` for explainability (PRD Step 9).

HARD RULE compliance: heavy/optional libraries (xgboost, numpy, sklearn, shap)
are imported LAZILY inside methods, wrapped in try/except, and every model path
has a deterministic stdlib heuristic fallback so the package imports and the
tests run with stdlib only (PRD Step 10 graceful degradation).
"""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from ...audit.decision_log import DecisionLogger
from ...models.domain import (
    BuildingImpact,
    EventKind,
    FireFront,
    RiskCell,
)
from ...models.geo import GridCell, LatLon

# Forecast horizons (PRD Step 3).
FLOOD_HORIZONS_MIN = (360, 720, 1440, 2880)  # T+6/12/24/48 h
FIRE_HORIZONS_MIN = (15, 30, 60)  # T+15/30/60 min

# Construction-class HAZUS-style fragility coefficients (Module B). Higher beta
# => higher collapse probability for a given shaking intensity. Indicative
# values tuned so kutcha << pucca << RCC in resilience.
FRAGILITY = {
    "kutcha": {"mmi_threshold": 5.0, "slope": 0.55},
    "pucca": {"mmi_threshold": 6.5, "slope": 0.40},
    "rcc": {"mmi_threshold": 7.5, "slope": 0.28},
    "unknown": {"mmi_threshold": 6.0, "slope": 0.45},
}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _logistic(x: float) -> float:
    """Numerically-safe logistic squash to (0, 1)."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _as_latlon(obj: Any, default: LatLon | None = None) -> LatLon:
    """Coerce a dict / LatLon / [lat, lon] into a :class:`LatLon`."""
    if isinstance(obj, LatLon):
        return obj
    if isinstance(obj, dict) and "lat" in obj and "lon" in obj:
        return LatLon(float(obj["lat"]), float(obj["lon"]))
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        return LatLon(float(obj[0]), float(obj[1]))
    return default or LatLon(0.0, 0.0)


def _extract_event(payload: dict) -> dict | None:
    """Pull the embedded DisasterEvent dict from a RAW_FEED payload, if any."""
    ev = payload.get("event")
    return ev if isinstance(ev, dict) else None


class _PredictionAgent(BaseAgent):
    """Shared Tier 2 prediction plumbing.

    Tier 2 SPECIALIST agents *do* hold decision authority within their domain
    (PRD Step 2); only Tier 3 sets ``decision_authority = False``. We subscribe
    to RAW_FEED and IOT_TELEMETRY and remember the last telemetry snapshot so a
    prediction can fuse live sensor readings with feed-driven events.
    """

    tier = Tier.SPECIALIST
    module: Module = Module.ALL

    def __init__(self, name: str, bus: MessageBus, logger: DecisionLogger | None = None) -> None:
        super().__init__(
            name=name,
            bus=bus,
            logger=logger,
            subscriptions=[Topic.RAW_FEED, Topic.IOT_TELEMETRY],
        )
        self._last_telemetry: dict[str, dict] = {}

    # --- helpers ---------------------------------------------------------
    def _remember_telemetry(self, message: Message) -> None:
        kind = str(message.payload.get("kind", "sensor"))
        self._last_telemetry[kind] = message.payload

    def _telemetry(self, kind: str) -> dict:
        return self._last_telemetry.get(kind, {})

    def _publish_prediction(
        self,
        incident_id: str | None,
        risk_cells: list[RiskCell],
        buildings: list[BuildingImpact],
        fire_fronts: list[FireFront],
        reasoning: list[str],
        priority: Priority,
    ) -> Message:
        payload = {
            "kind": "risk",
            "incident_id": incident_id,
            "module": self.module.value,
            "risk_cells": [asdict(c) for c in risk_cells],
            "buildings": [asdict(b) for b in buildings],
            "fire_fronts": [asdict(f) for f in fire_fronts],
        }
        return Message(
            sender=self.name,
            recipient="tier2.cascade",
            type=MessageType.ALERT,
            priority=priority,
            payload=payload,
            reasoning=reasoning,
            topic=Topic.PREDICTION,
            incident_id=incident_id,
            module=self.module,
        )


# --------------------------------------------------------------------------- A
class CyclonePredictionAgent(_PredictionAgent):
    """Module A flood/cyclone inundation forecaster (PRD Step 3 Module A).

    Production interface (lazy): an XGBoost tabular model over hydro-met drivers
    fused with a U-Net CNN inundation raster — an ensemble. When those libraries
    are absent (stdlib-only test environment) we fall back to a deterministic
    inundation-probability heuristic over a 100 m :class:`GridCell` lattice at
    horizons T+6/12/24/48 h, plus population_at_risk.
    """

    module = Module.CYCLONE_FLOOD

    def __init__(self, bus: MessageBus, logger: DecisionLogger | None = None) -> None:
        super().__init__("tier2.prediction.cyclone", bus, logger)

    def handle(self, message: Message) -> list[Message]:
        if message.topic == Topic.IOT_TELEMETRY:
            self._remember_telemetry(message)
            return []
        if message.topic != Topic.RAW_FEED:
            return []
        event = _extract_event(message.payload)
        if not event:
            return []
        kind = str(event.get("kind", "")).lower()
        if kind not in (EventKind.CYCLONE.value, EventKind.FLOOD.value):
            return []
        return self._forecast(message, event)

    # -- forecasting ------------------------------------------------------
    def _forecast(self, message: Message, event: dict) -> list[Message]:
        incident_id = event.get("incident_id") or message.incident_id
        epicentre = _as_latlon(event.get("epicentre"))
        severity = float(event.get("severity", 1.0))  # cyclone category / flood scale
        meta = event.get("meta", {}) or {}

        # Hydro-met drivers: prefer live IoT telemetry, then event meta, defaults.
        water = self._telemetry("water_level")
        rain = self._telemetry("rain_gauge")
        rainfall_mm = float(
            rain.get("rainfall_mm", meta.get("rainfall_mm", 50.0 + 30.0 * severity))
        )
        surge_m = float(meta.get("storm_surge_m", max(0.0, severity - 1.0)))
        river_level_m = float(
            water.get("level_m", meta.get("river_level_m", 2.0 + 0.6 * severity))
        )
        observations = message.payload.get("observations", []) or []

        cells, attrib = self._predict_cells(
            epicentre, rainfall_mm, surge_m, river_level_m, severity, observations
        )

        self.logger.log_prediction(
            model=attrib["model"],
            inputs={
                "rainfall_mm": rainfall_mm,
                "storm_surge_m": surge_m,
                "river_level_m": river_level_m,
                "severity": severity,
                "epicentre": asdict(epicentre),
            },
            prediction={
                "n_cells": len(cells),
                "max_probability": max((c.probability for c in cells), default=0.0),
                "population_at_risk": sum(c.population_at_risk for c in cells),
            },
            shap=attrib["shap"],
            incident_id=incident_id,
        )

        peak = max((c.probability for c in cells), default=0.0)
        reasoning = [
            f"{attrib['model']} flood forecast for incident {incident_id}",
            f"drivers: rainfall={rainfall_mm:.0f}mm surge={surge_m:.1f}m "
            f"river={river_level_m:.1f}m severity={severity:.1f}",
            f"{len(cells)} risk cells across T+6/12/24/48h; peak P(inundation)={peak:.2f}",
            f"population at risk={sum(c.population_at_risk for c in cells)}",
        ]
        priority = Priority.CRITICAL if peak >= 0.6 else Priority.HIGH
        return [self._publish_prediction(incident_id, cells, [], [], reasoning, priority)]

    def _predict_cells(
        self,
        epicentre: LatLon,
        rainfall_mm: float,
        surge_m: float,
        river_level_m: float,
        severity: float,
        observations: list,
    ) -> tuple[list[RiskCell], dict]:
        """Try ensemble (XGBoost + U-Net), else deterministic heuristic."""
        ensemble = self._try_ensemble(
            epicentre, rainfall_mm, surge_m, river_level_m, severity, observations
        )
        if ensemble is not None:
            return ensemble
        return self._heuristic_cells(
            epicentre, rainfall_mm, surge_m, river_level_m, severity, observations
        )

    def _try_ensemble(self, *args) -> tuple[list[RiskCell], dict] | None:
        """Lazy XGBoost + U-Net ensemble. Returns None to trigger fallback."""
        try:
            import numpy as np  # noqa: F401
            import xgboost  # noqa: F401  # type: ignore
        except Exception:
            return None
        # Real ensemble (trained boosters + CNN weights) would be wired here.
        # No artefacts ship with the repo, so signal fallback deterministically.
        return None

    def _heuristic_cells(
        self,
        epicentre: LatLon,
        rainfall_mm: float,
        surge_m: float,
        river_level_m: float,
        severity: float,
        observations: list,
    ) -> tuple[list[RiskCell], dict]:
        """Deterministic per-100m-cell inundation heuristic (stdlib fallback).

        Base hazard rises with rainfall, surge and river level; probability
        decays with distance from the epicentre and grows toward later horizons
        as accumulated water spreads. population_at_risk scales the local
        baseline by the cell probability.
        """
        size_m = 100
        # Normalised driver scores in [0, 1].
        s_rain = _clamp01(rainfall_mm / 300.0)
        s_surge = _clamp01(surge_m / 6.0)
        s_river = _clamp01((river_level_m - 1.0) / 7.0)
        base = 0.45 * s_rain + 0.30 * s_surge + 0.25 * s_river

        # Per-driver SHAP-style attribution toward the peak (T+24h, centre) cell.
        shap = {
            "rainfall_mm": round(0.45 * s_rain, 4),
            "storm_surge_m": round(0.30 * s_surge, 4),
            "river_level_m": round(0.25 * s_river, 4),
        }

        # 5x5 lattice of 100 m cells centred on the epicentre.
        cells: list[RiskCell] = []
        baseline_pop = int(meta_population(observations, default=800))
        radius = 2
        for hi, horizon in enumerate(FLOOD_HORIZONS_MIN):
            # Spread factor grows with horizon (accumulation), saturating.
            spread = 0.6 + 0.4 * (hi / max(1, len(FLOOD_HORIZONS_MIN) - 1))
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    dist_cells = math.hypot(dr, dc)
                    decay = math.exp(-dist_cells / (2.0 + 2.0 * spread))
                    prob = _clamp01(base * spread * decay)
                    if prob < 0.05:
                        continue
                    centroid = _offset_latlon(epicentre, dr * size_m, dc * size_m)
                    cell = GridCell.from_latlon(centroid, size_m=size_m, origin=epicentre)
                    pop_at_risk = int(baseline_pop * prob)
                    cells.append(
                        RiskCell(
                            cell_id=f"{cell.id}@{horizon}",
                            centroid=centroid,
                            probability=round(prob, 4),
                            horizon_minutes=horizon,
                            population_at_risk=pop_at_risk,
                            shap=shap,
                        )
                    )
        return cells, {"model": "flood-inundation-heuristic", "shap": shap}


# --------------------------------------------------------------------------- B
class EarthquakeImpactAgent(_PredictionAgent):
    """Module B earthquake structural-impact assessor (PRD Step 3 Module B).

    Production interface (lazy): HAZUS-style fragility curves per building +
    Poisson casualty model. Fallback derives a collapse probability from a
    ShakeMap MMI field using construction-class fragility coefficients, then
    estimates trapped occupants via a Poisson-mean heuristic, yielding a
    ``BuildingImpact`` list and rescue-priority RiskCells.
    """

    module = Module.EARTHQUAKE

    def __init__(self, bus: MessageBus, logger: DecisionLogger | None = None) -> None:
        super().__init__("tier2.prediction.earthquake", bus, logger)

    def handle(self, message: Message) -> list[Message]:
        if message.topic == Topic.IOT_TELEMETRY:
            self._remember_telemetry(message)
            return []
        if message.topic != Topic.RAW_FEED:
            return []
        event = _extract_event(message.payload)
        if not event:
            return []
        if str(event.get("kind", "")).lower() != EventKind.EARTHQUAKE.value:
            return []
        return self._assess(message, event)

    def _assess(self, message: Message, event: dict) -> list[Message]:
        incident_id = event.get("incident_id") or message.incident_id
        epicentre = _as_latlon(event.get("epicentre"))
        magnitude = float(event.get("severity", 5.0))
        meta = event.get("meta", {}) or {}
        depth_km = float(meta.get("depth_km", 10.0))

        # Buildings to assess come from the feed observations (inventory) or a
        # synthetic mixed-construction sample around the epicentre.
        buildings_in = message.payload.get("observations") or meta.get("buildings") or []
        if not buildings_in:
            buildings_in = _synthetic_building_inventory(epicentre)

        impacts, zones, attrib = self._assess_buildings(
            epicentre, magnitude, depth_km, buildings_in
        )

        total_trapped = sum(b.estimated_trapped for b in impacts)
        peak_collapse = max((b.collapse_probability for b in impacts), default=0.0)

        self.logger.log_prediction(
            model=attrib["model"],
            inputs={
                "magnitude": magnitude,
                "depth_km": depth_km,
                "n_buildings": len(impacts),
                "epicentre": asdict(epicentre),
            },
            prediction={
                "n_collapses_expected": round(
                    sum(b.collapse_probability for b in impacts), 2
                ),
                "estimated_trapped": total_trapped,
                "peak_collapse_probability": round(peak_collapse, 4),
            },
            shap=attrib["shap"],
            incident_id=incident_id,
        )

        reasoning = [
            f"{attrib['model']} HAZUS-style assessment for incident {incident_id}",
            f"M{magnitude:.1f} depth={depth_km:.0f}km over {len(impacts)} buildings",
            f"expected collapses={sum(b.collapse_probability for b in impacts):.1f}, "
            f"estimated trapped={total_trapped}",
            f"{len(zones)} rescue-priority zones (peak P(collapse)={peak_collapse:.2f})",
        ]
        priority = Priority.CRITICAL if peak_collapse >= 0.4 or total_trapped >= 25 else Priority.HIGH
        return [self._publish_prediction(incident_id, zones, impacts, [], reasoning, priority)]

    def _assess_buildings(
        self, epicentre: LatLon, magnitude: float, depth_km: float, buildings_in: list
    ) -> tuple[list[BuildingImpact], list[RiskCell], dict]:
        hazus = self._try_hazus(epicentre, magnitude, depth_km, buildings_in)
        if hazus is not None:
            return hazus
        return self._heuristic_buildings(epicentre, magnitude, depth_km, buildings_in)

    def _try_hazus(self, *args) -> tuple[list[BuildingImpact], list[RiskCell], dict] | None:
        """Lazy HAZUS/sklearn fragility model. Returns None to trigger fallback."""
        try:
            import numpy as np  # noqa: F401
            import sklearn  # noqa: F401  # type: ignore
        except Exception:
            return None
        return None  # no fitted fragility artefacts ship with the repo

    def _heuristic_buildings(
        self, epicentre: LatLon, magnitude: float, depth_km: float, buildings_in: list
    ) -> tuple[list[BuildingImpact], list[RiskCell], dict]:
        """ShakeMap-MMI fragility fallback + Poisson casualty heuristic."""
        impacts: list[BuildingImpact] = []
        zone_acc: dict[str, dict] = {}
        shap_accum = {"magnitude": 0.0, "distance_km": 0.0, "construction": 0.0}
        n = 0

        for raw in buildings_in:
            loc = _as_latlon(raw.get("location") if isinstance(raw, dict) else None, epicentre)
            construction = str(
                (raw.get("construction") if isinstance(raw, dict) else None) or "unknown"
            ).lower()
            occupants = int((raw.get("occupants") if isinstance(raw, dict) else 0) or 6)
            bid = str(
                (raw.get("building_id") if isinstance(raw, dict) else None) or f"bld-{n}"
            )

            dist_km = epicentre.distance_m(loc) / 1000.0
            mmi = _mmi_from_magnitude(magnitude, dist_km, depth_km)
            frag = FRAGILITY.get(construction, FRAGILITY["unknown"])
            # Logistic fragility: P(collapse) = sigmoid(slope*(MMI - threshold)).
            collapse = _clamp01(_logistic(frag["slope"] * 8.0 * (mmi - frag["mmi_threshold"]) / 4.0))
            # Poisson mean trapped = occupants * collapse * entrapment factor.
            lam = occupants * collapse * 0.55
            trapped = int(round(lam))

            impacts.append(
                BuildingImpact(
                    building_id=bid,
                    location=loc,
                    collapse_probability=round(collapse, 4),
                    estimated_trapped=trapped,
                    construction=construction,
                )
            )

            # Per-feature attribution proportional to driver contribution.
            shap_accum["magnitude"] += min(1.0, magnitude / 9.0) * collapse
            shap_accum["distance_km"] += (1.0 / (1.0 + dist_km)) * collapse
            shap_accum["construction"] += frag["slope"] * collapse
            n += 1

            cell = GridCell.from_latlon(loc, size_m=100, origin=epicentre)
            z = zone_acc.setdefault(
                cell.id,
                {"centroid": loc, "prob_sum": 0.0, "count": 0, "trapped": 0},
            )
            z["prob_sum"] += collapse
            z["count"] += 1
            z["trapped"] += trapped

        # Rescue-priority zones expressed as RiskCells (probability = mean collapse).
        zones: list[RiskCell] = []
        for cid, z in zone_acc.items():
            mean_p = z["prob_sum"] / z["count"] if z["count"] else 0.0
            zones.append(
                RiskCell(
                    cell_id=cid,
                    centroid=z["centroid"],
                    probability=round(mean_p, 4),
                    horizon_minutes=0,  # immediate post-quake assessment
                    population_at_risk=z["trapped"],
                    shap={"buildings_in_cell": z["count"]},
                )
            )
        zones.sort(key=lambda c: (c.probability, c.population_at_risk), reverse=True)

        denom = max(1.0, sum(shap_accum.values()))
        shap = {k: round(v / denom, 4) for k, v in shap_accum.items()}
        return impacts, zones, {"model": "shakemap-fragility-heuristic", "shap": shap}


# --------------------------------------------------------------------------- C
class FireSpreadAgent(_PredictionAgent):
    """Module C urban-fire spread forecaster (PRD Step 3 Module C).

    Production interface (lazy): cellular-automata fire spread on a fuel/wind/
    slope grid. The stdlib fallback runs a deterministic CA over a small grid
    seeded at the ignition point, producing a fire perimeter at T+15/30/60 min
    as a :class:`FireFront` list, and tags critical infrastructure each
    perimeter threatens.
    """

    module = Module.FIRE_COLLAPSE

    def __init__(self, bus: MessageBus, logger: DecisionLogger | None = None) -> None:
        super().__init__("tier2.prediction.fire", bus, logger)

    def handle(self, message: Message) -> list[Message]:
        if message.topic == Topic.IOT_TELEMETRY:
            self._remember_telemetry(message)
            return []
        if message.topic != Topic.RAW_FEED:
            return []
        event = _extract_event(message.payload)
        if not event:
            return []
        kind = str(event.get("kind", "")).lower()
        if kind not in (EventKind.URBAN_FIRE.value, EventKind.STRUCTURAL_COLLAPSE.value):
            return []
        return self._spread(message, event)

    def _spread(self, message: Message, event: dict) -> list[Message]:
        incident_id = event.get("incident_id") or message.incident_id
        ignition = _as_latlon(event.get("epicentre"))
        intensity = float(event.get("severity", 1.0))
        meta = event.get("meta", {}) or {}

        # Wind drives the spread vector; prefer live IoT, then meta, then calm.
        wind = self._telemetry("wind")
        wind_speed = float(wind.get("speed_ms", meta.get("wind_speed_ms", 3.0)))
        wind_dir_deg = float(wind.get("dir_deg", meta.get("wind_dir_deg", 90.0)))
        infra = meta.get("critical_infrastructure") or message.payload.get("observations") or []

        fronts, attrib = self._spread_fronts(
            ignition, intensity, wind_speed, wind_dir_deg, infra
        )

        self.logger.log_prediction(
            model=attrib["model"],
            inputs={
                "intensity": intensity,
                "wind_speed_ms": wind_speed,
                "wind_dir_deg": wind_dir_deg,
                "ignition": asdict(ignition),
            },
            prediction={
                "n_fronts": len(fronts),
                "max_perimeter_points": max((len(f.perimeter) for f in fronts), default=0),
                "infra_threatened": sorted(
                    {c for f in fronts for c in f.critical_infrastructure}
                ),
            },
            shap=attrib["shap"],
            incident_id=incident_id,
        )

        threatened = sorted({c for f in fronts for c in f.critical_infrastructure})
        reasoning = [
            f"{attrib['model']} fire-perimeter projection for incident {incident_id}",
            f"intensity={intensity:.1f} wind={wind_speed:.1f}m/s @ {wind_dir_deg:.0f}deg",
            f"fronts at T+15/30/60min; critical infra threatened={threatened or 'none'}",
        ]
        priority = Priority.CRITICAL if threatened or intensity >= 2.0 else Priority.HIGH
        return [self._publish_prediction(incident_id, [], [], fronts, reasoning, priority)]

    def _spread_fronts(
        self,
        ignition: LatLon,
        intensity: float,
        wind_speed: float,
        wind_dir_deg: float,
        infra: list,
    ) -> tuple[list[FireFront], dict]:
        ca = self._try_ca(ignition, intensity, wind_speed, wind_dir_deg, infra)
        if ca is not None:
            return ca
        return self._heuristic_fronts(ignition, intensity, wind_speed, wind_dir_deg, infra)

    def _try_ca(self, *args) -> tuple[list[FireFront], dict] | None:
        """Lazy numpy-accelerated CA. Returns None to fall back to stdlib CA."""
        try:
            import numpy as np  # noqa: F401
        except Exception:
            return None
        # The stdlib CA below is already deterministic and dependency-free; we
        # only branch to numpy when a large grid is needed. Default to fallback.
        return None

    def _heuristic_fronts(
        self,
        ignition: LatLon,
        intensity: float,
        wind_speed: float,
        wind_dir_deg: float,
        infra: list,
    ) -> tuple[list[FireFront], dict]:
        """Deterministic cellular-automata fire perimeter (stdlib fallback).

        The fire spreads outward at a base rate (m/min) boosted in the
        downwind direction. We grow an elliptical perimeter and, at each
        horizon, sample it as a polygon of LatLon points; infrastructure inside
        the perimeter is flagged as threatened.
        """
        # Base rate-of-spread in metres/minute, scaled by intensity and wind.
        base_ros = 4.0 + 6.0 * _clamp01(intensity / 3.0) + 1.5 * wind_speed
        wind_dir = math.radians(wind_dir_deg)
        eccentric = 1.0 + 0.15 * wind_speed  # downwind elongation factor

        infra_points = _parse_infra(infra)
        fronts: list[FireFront] = []
        n_pts = 16
        for horizon in FIRE_HORIZONS_MIN:
            base_radius = base_ros * horizon  # metres
            perimeter: list[LatLon] = []
            threatened: list[str] = []
            for k in range(n_pts):
                theta = 2 * math.pi * k / n_pts
                # Elliptical radius: longer downwind, shorter upwind.
                downwind_align = math.cos(theta - wind_dir)
                r = base_radius * (1.0 + 0.5 * (eccentric - 1.0) * downwind_align)
                r = max(1.0, r)
                north_m = r * math.cos(theta)
                east_m = r * math.sin(theta)
                perimeter.append(_offset_latlon(ignition, north_m, east_m))
            # Flag infra within the max radius of this front.
            for name, pt in infra_points:
                if ignition.distance_m(pt) <= base_radius * eccentric:
                    threatened.append(name)
            fronts.append(
                FireFront(
                    horizon_minutes=horizon,
                    perimeter=perimeter,
                    critical_infrastructure=sorted(set(threatened)),
                )
            )

        # SHAP-style attribution for the rate-of-spread drivers.
        denom = base_ros if base_ros else 1.0
        shap = {
            "intensity": round((6.0 * _clamp01(intensity / 3.0)) / denom, 4),
            "wind_speed_ms": round((1.5 * wind_speed) / denom, 4),
            "base_fuel": round(4.0 / denom, 4),
        }
        return fronts, {"model": "fire-cellular-automata-heuristic", "shap": shap}


# --------------------------------------------------------------------------- utils
def _offset_latlon(origin: LatLon, north_m: float, east_m: float) -> LatLon:
    """Translate a point by (north, east) metres (equirectangular approx.)."""
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(origin.lat)) or 1e-6
    return LatLon(
        origin.lat + north_m / m_per_deg_lat,
        origin.lon + east_m / m_per_deg_lon,
    )


def _mmi_from_magnitude(magnitude: float, dist_km: float, depth_km: float) -> float:
    """ShakeMap-style intensity attenuation (PRD Step 3 Module B fallback).

    Modified Mercalli Intensity decays with hypocentral distance. Simplified
    Bakun-Wentworth-style relation, clamped to the 1..12 MMI range.
    """
    hypo = math.sqrt(dist_km * dist_km + depth_km * depth_km)
    mmi = 1.5 * magnitude - 1.4 * math.log10(max(1.0, hypo)) - 0.5
    return max(1.0, min(12.0, mmi))


def meta_population(observations: list, default: int) -> int:
    """Best-effort baseline population from feed observations."""
    for obs in observations or []:
        if isinstance(obs, dict) and "population" in obs:
            try:
                return int(obs["population"])
            except (TypeError, ValueError):
                continue
    return default


def _synthetic_building_inventory(epicentre: LatLon) -> list[dict]:
    """Mixed-construction sample inventory around the epicentre (dry-run path)."""
    classes = ["kutcha", "pucca", "rcc", "kutcha", "pucca"]
    inv: list[dict] = []
    for i, c in enumerate(classes):
        loc = _offset_latlon(epicentre, (i - 2) * 120.0, ((i % 3) - 1) * 90.0)
        inv.append(
            {
                "building_id": f"syn-{i}",
                "construction": c,
                "occupants": 8 + 4 * i,
                "location": asdict(loc),
            }
        )
    return inv


def _parse_infra(infra: list) -> list[tuple[str, LatLon]]:
    """Normalise critical-infra entries into (name, LatLon) pairs."""
    out: list[tuple[str, LatLon]] = []
    for item in infra or []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("id") or "infra")
            loc = item.get("location")
            if loc is not None:
                out.append((name, _as_latlon(loc)))
    return out
