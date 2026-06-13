"""Module A — cyclone / flood inundation forecaster (PRD Step 3 Module A)."""
from __future__ import annotations

import math
from dataclasses import asdict

from ....audit.decision_log import DecisionLogger
from ....core.bus import MessageBus
from ....core.contracts import (
    Message,
    Module,
    Priority,
    Topic,
)
from ....models.domain import (
    EventKind,
    RiskCell,
)
from ....models.geo import GridCell, LatLon
from .base import (
    FLOOD_HORIZONS_MIN,
    _PredictionAgent,
    _as_latlon,
    _clamp01,
    _extract_event,
    _offset_latlon,
)


def meta_population(observations: list, default: int) -> int:
    """Best-effort baseline population from feed observations."""
    for obs in observations or []:
        if isinstance(obs, dict) and "population" in obs:
            try:
                return int(obs["population"])
            except (TypeError, ValueError):
                continue
    return default


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
        return [self._publish_prediction(incident_id, cells, [], [], reasoning, priority, shap=attrib["shap"])]

    def _predict_cells(
        self,
        epicentre: LatLon,
        rainfall_mm: float,
        surge_m: float,
        river_level_m: float,
        severity: float,
        observations: list,
    ) -> tuple[list[RiskCell], dict]:
        """Try real model (XGBoost ensemble), else deterministic heuristic."""
        ensemble = self._try_ensemble(
            epicentre, rainfall_mm, surge_m, river_level_m, severity, observations
        )
        if ensemble is not None:
            return ensemble
        return self._heuristic_cells(
            epicentre, rainfall_mm, surge_m, river_level_m, severity, observations
        )

    def _try_ensemble(
        self,
        epicentre: LatLon,
        rainfall_mm: float,
        surge_m: float,
        river_level_m: float,
        severity: float,
        observations: list,
    ) -> tuple[list[RiskCell], dict] | None:
        """Real model layer (:mod:`disastermind.ml`) inundation path.

        Only engages when a *trained real backend* is loaded for Module A
        (``model._backend_obj is not None``). It then derives the base hazard
        probability from ``model.predict_one`` and sources the SHAP attribution
        from the real explainer, reusing the SAME spreading lattice as the
        heuristic via ``base_override``. With no trained artefact (the default —
        nothing ships) it returns ``None`` so the deterministic heuristic runs
        byte-identically.
        """
        try:
            from .... import ml  # lazy: keep stdlib import path clean
        except Exception:
            return None
        try:
            model = ml.get_model(self.module)
            if getattr(model, "_backend_obj", None) is None:
                return None  # no trained backend -> deterministic fallback
            raw = {
                "rainfall_mm": rainfall_mm,
                "storm_surge_m": surge_m,
                "river_level_m": river_level_m,
            }
            fv = ml.features.features_for_module(self.module, raw)
            base = float(model.predict_one(fv))
            shap = ml.shap_explain.explain_dict(model, fv)
        except Exception:
            return None
        return self._heuristic_cells(
            epicentre,
            rainfall_mm,
            surge_m,
            river_level_m,
            severity,
            observations,
            base_override=base,
            shap_override=shap,
            model_name="flood-inundation-ml",
        )

    def _heuristic_cells(
        self,
        epicentre: LatLon,
        rainfall_mm: float,
        surge_m: float,
        river_level_m: float,
        severity: float,
        observations: list,
        base_override: float | None = None,
        shap_override: dict | None = None,
        model_name: str = "flood-inundation-heuristic",
    ) -> tuple[list[RiskCell], dict]:
        """Deterministic per-100m-cell inundation spread (ONE shared impl).

        Base hazard rises with rainfall, surge and river level; probability
        decays with distance from the epicentre and grows toward later horizons
        as accumulated water spreads. population_at_risk scales the local
        baseline by the cell probability. When ``base_override`` is supplied (the
        real-model path) it replaces the heuristic base probability and
        ``shap_override`` replaces the per-driver attribution, while the spatial
        spreading lattice below is shared verbatim by both paths.
        """
        size_m = 100
        # Normalised driver scores in [0, 1].
        s_rain = _clamp01(rainfall_mm / 300.0)
        s_surge = _clamp01(surge_m / 6.0)
        s_river = _clamp01((river_level_m - 1.0) / 7.0)
        base = 0.45 * s_rain + 0.30 * s_surge + 0.25 * s_river
        if base_override is not None:
            base = _clamp01(float(base_override))

        # Per-driver SHAP-style attribution toward the peak (T+24h, centre) cell.
        shap = {
            "rainfall_mm": round(0.45 * s_rain, 4),
            "storm_surge_m": round(0.30 * s_surge, 4),
            "river_level_m": round(0.25 * s_river, 4),
        }
        if shap_override is not None:
            shap = {k: round(float(v), 4) for k, v in shap_override.items()}

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
        return cells, {"model": model_name, "shap": shap}
