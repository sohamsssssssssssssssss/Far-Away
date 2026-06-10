"""Real-data validation of ALL THREE hazard models (PRD Step 3, done honestly).

Validates against committed REAL historical fixtures — the USGS earthquake
catalog, GloFAS-ERA5 river discharge + ERA5 rainfall for Indian basins, and
USDA FPA-FOD wildfire occurrences + ERA5 fire weather — with leak-free
features, temporal splits, operational baselines, blocked spatial/temporal CV,
calibrated uncertainty, fairness audits, tail analysis and drift monitoring.
No synthetic data anywhere in this package. See
:mod:`disastermind.ml.validation.run` for the full battery and
:mod:`disastermind.ml.validation.fetch` for fixture provenance/refresh.
"""
from __future__ import annotations

from .dataset import (  # noqa: F401
    FEATURE_NAMES,
    Quake,
    load_quakes,
    temporal_split,
    to_xy,
)
from .run import (  # noqa: F401
    HAZARDS,
    HazardSpec,
    evaluate_hazard,
    fire_spec,
    fit_logistic,
    flood_spec,
    predict,
    quake_felt_vs_pager,
    quake_spec,
    run_validation,
    to_markdown,
)

__all__ = [
    "FEATURE_NAMES",
    "Quake",
    "load_quakes",
    "temporal_split",
    "to_xy",
    "fit_logistic",
    "predict",
    "run_validation",
    "to_markdown",
    "HAZARDS",
    "HazardSpec",
    "evaluate_hazard",
    "quake_spec",
    "flood_spec",
    "fire_spec",
    "quake_felt_vs_pager",
]
