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

This package was split out of a single ``agents.py`` module (PRD Step 3); the
public surface is re-exported here so ``disastermind.tier2.prediction.agents``
remains import-compatible.
"""
from __future__ import annotations

from .base import (
    FIRE_HORIZONS_MIN,
    FLOOD_HORIZONS_MIN,
    FRAGILITY,
    _PredictionAgent,
    _as_latlon,
    _clamp01,
    _extract_event,
    _logistic,
    _offset_latlon,
    _shap_features,
)
from .cyclone import CyclonePredictionAgent, meta_population
from .earthquake import (
    EarthquakeImpactAgent,
    _mmi_from_magnitude,
    _synthetic_building_inventory,
)
from .fire import FireSpreadAgent, _infra_density, _parse_infra

__all__ = [
    "CyclonePredictionAgent",
    "EarthquakeImpactAgent",
    "FireSpreadAgent",
    "meta_population",
]
