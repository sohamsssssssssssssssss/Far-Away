"""disastermind.ml — the real model layer behind the prediction heuristics.

PRD Step 3 (Prediction & Assessment) / Step 9 (Explainability). This package
turns domain drivers into feature vectors, runs them through per-module risk
models (XGBoost / scikit-learn) and explains the result with SHAP — each step
backed by a deterministic stdlib fallback so the system never depends on an
optional library or the network (PRD Step 10):

  * :mod:`~disastermind.ml.features`  — domain inputs -> ordered feature vectors.
  * :mod:`~disastermind.ml.models`    — model wrappers with heuristic fallback.
  * :mod:`~disastermind.ml.shap_explain` — SHAP attributions / stdlib fallback.
  * :mod:`~disastermind.ml.registry`  — ``get_model(module)`` per A/B/C.

All public symbols are re-exported here for a flat import surface.
"""
from __future__ import annotations

from .features import (
    CONSTRUCTION_ORDINAL,
    FEATURE_NAMES,
    FeatureError,
    FeatureVector,
    feature_matrix,
    features_for_event,
    features_for_module,
    fire_features,
    flood_features,
    quake_features,
)
from .models import (
    HeuristicRiskModel,
    RiskModel,
    SklearnRiskModel,
    XGBoostRiskModel,
    heuristic_probability,
)
from .registry import (
    DEFAULT_BACKENDS,
    all_models,
    get_model,
    load_model,
    model_class_for,
    register_model,
    reset_registry,
)
from .shap_explain import (
    Explanation,
    baseline_row,
    explain,
    explain_dict,
)

__all__ = [
    # features
    "FEATURE_NAMES",
    "CONSTRUCTION_ORDINAL",
    "FeatureVector",
    "FeatureError",
    "quake_features",
    "flood_features",
    "fire_features",
    "features_for_module",
    "features_for_event",
    "feature_matrix",
    # models
    "RiskModel",
    "XGBoostRiskModel",
    "SklearnRiskModel",
    "HeuristicRiskModel",
    "heuristic_probability",
    # registry
    "get_model",
    "register_model",
    "load_model",
    "all_models",
    "model_class_for",
    "reset_registry",
    "DEFAULT_BACKENDS",
    # explainability
    "Explanation",
    "explain",
    "explain_dict",
    "baseline_row",
]
