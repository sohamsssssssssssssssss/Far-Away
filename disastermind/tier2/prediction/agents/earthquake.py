"""Module B — earthquake structural-impact assessor (PRD Step 3 Module B)."""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

from ....audit.decision_log import DecisionLogger
from ....core.bus import MessageBus
from ....core.contracts import (
    Message,
    Module,
    Priority,
    Topic,
)
from ....models.domain import (
    BuildingImpact,
    EventKind,
    RiskCell,
)
from ....models.geo import GridCell, LatLon
from .base import (
    FRAGILITY,
    _PredictionAgent,
    _as_latlon,
    _clamp01,
    _extract_event,
    _logistic,
    _offset_latlon,
)


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
        return [self._publish_prediction(incident_id, zones, impacts, [], reasoning, priority, shap=attrib["shap"])]

    def _assess_buildings(
        self, epicentre: LatLon, magnitude: float, depth_km: float, buildings_in: list
    ) -> tuple[list[BuildingImpact], list[RiskCell], dict]:
        hazus = self._try_hazus(epicentre, magnitude, depth_km, buildings_in)
        if hazus is not None:
            return hazus
        return self._heuristic_buildings(epicentre, magnitude, depth_km, buildings_in)

    def _try_hazus(
        self, epicentre: LatLon, magnitude: float, depth_km: float, buildings_in: list
    ) -> tuple[list[BuildingImpact], list[RiskCell], dict] | None:
        """Real model layer (:mod:`disastermind.ml`) HAZUS fragility path.

        Only engages when a *trained real backend* is loaded for Module B
        (``model._backend_obj is not None``). The per-building collapse
        probability is then derived from ``model.predict_one`` over the
        ``(magnitude, distance_km, construction)`` feature vector, and the SHAP
        attribution comes from the real explainer (keyed on the peak building),
        while the SAME structural-spreading body (Poisson casualties + rescue
        zones) is reused via ``base_override``. With no trained artefact (the
        default — nothing ships) it returns ``None`` so the deterministic
        ShakeMap-MMI fragility heuristic runs byte-identically.
        """
        try:
            from .... import ml  # lazy: keep stdlib import path clean
        except Exception:
            return None
        try:
            model = ml.get_model(self.module)
            if getattr(model, "_backend_obj", None) is None:
                return None  # no trained backend -> deterministic fallback
            features_for_module = ml.features.features_for_module
            explain_dict = ml.shap_explain.explain_dict
        except Exception:
            return None

        def _model_collapse(dist_km: float, construction: str) -> float:
            raw = {
                "magnitude": magnitude,
                "distance_km": dist_km,
                "construction": construction,
            }
            fv = features_for_module(self.module, raw)
            return _clamp01(float(model.predict_one(fv)))

        def _model_shap(dist_km: float, construction: str) -> dict:
            raw = {
                "magnitude": magnitude,
                "distance_km": dist_km,
                "construction": construction,
            }
            fv = features_for_module(self.module, raw)
            return explain_dict(model, fv)

        try:
            return self._heuristic_buildings(
                epicentre,
                magnitude,
                depth_km,
                buildings_in,
                base_override=_model_collapse,
                shap_override=_model_shap,
                model_name="shakemap-fragility-ml",
            )
        except Exception:
            return None

    def _heuristic_buildings(
        self,
        epicentre: LatLon,
        magnitude: float,
        depth_km: float,
        buildings_in: list,
        base_override: Any | None = None,
        shap_override: Any | None = None,
        model_name: str = "shakemap-fragility-heuristic",
    ) -> tuple[list[BuildingImpact], list[RiskCell], dict]:
        """ShakeMap-MMI fragility + Poisson casualty spread (ONE shared impl).

        ``base_override`` (when supplied by the real-model path) is a callable
        ``(distance_km, construction) -> P(collapse)`` that replaces the logistic
        fragility formula; the surrounding Poisson-casualty and rescue-zone
        spreading code is shared verbatim by both paths. ``shap_override`` is a
        callable that yields the real model's attribution dict for the peak
        building.
        """
        impacts: list[BuildingImpact] = []
        zone_acc: dict[str, dict] = {}
        shap_accum = {"magnitude": 0.0, "distance_km": 0.0, "construction": 0.0}
        peak_for_shap: tuple[float, float, str] | None = None
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
            if base_override is not None:
                collapse = _clamp01(float(base_override(dist_km, construction)))
            else:
                # Logistic fragility: P(collapse) = sigmoid(slope*(MMI - threshold)).
                collapse = _clamp01(_logistic(frag["slope"] * 8.0 * (mmi - frag["mmi_threshold"]) / 4.0))
            if peak_for_shap is None or collapse > peak_for_shap[0]:
                peak_for_shap = (collapse, dist_km, construction)
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
            n += 1  # noqa: SIM113

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
        if shap_override is not None and peak_for_shap is not None:
            try:
                _, peak_dist, peak_constr = peak_for_shap
                shap = {
                    k: round(float(v), 4)
                    for k, v in shap_override(peak_dist, peak_constr).items()
                }
            except Exception:
                pass
        return impacts, zones, {"model": model_name, "shap": shap}


# --------------------------------------------------------------------------- utils
def _mmi_from_magnitude(magnitude: float, dist_km: float, depth_km: float) -> float:
    """ShakeMap-style intensity attenuation (PRD Step 3 Module B fallback).

    Modified Mercalli Intensity decays with hypocentral distance. Simplified
    Bakun-Wentworth-style relation, clamped to the 1..12 MMI range.
    """
    hypo = math.sqrt(dist_km * dist_km + depth_km * depth_km)
    mmi = 1.5 * magnitude - 1.4 * math.log10(max(1.0, hypo)) - 0.5
    return max(1.0, min(12.0, mmi))


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
