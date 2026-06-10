"""Explainability — SHAP attributions with a deterministic stdlib fallback.

PRD Step 9 (Explainability). The tier-2 prediction agents already log a SHAP
dict via :meth:`DecisionLogger.log_prediction` whose shape is ``{feature_name:
float}`` — one signed attribution per driver, keyed by the same names the model
layer uses (:data:`disastermind.ml.features.FEATURE_NAMES`). This module produces
that exact dict shape for an arbitrary :class:`RiskModel` + feature vector.

Two paths, selected lazily and degrading gracefully (PRD Step 10):

  1. SHAP (``import shap``, only if a real fitted backend is present) — real
     Shapley values from a :class:`~shap.Explainer`, reduced to one value per
     feature for the explained row.
  2. Deterministic stdlib FALLBACK (always available) — a finite-difference /
     marginal-contribution attribution computed against the model's *own*
     ``predict`` by toggling each feature to a per-module baseline. The result is
     normalised so attributions sum to the (prediction - base) gap, matching the
     additive shape SHAP guarantees and the dict shape agents log. NO optional
     dependency, NO network, fully deterministic.

The fallback works for ANY :class:`RiskModel` (heuristic or real) because it only
calls ``predict``; this means explanations stay available even when ``shap``
itself is installed but the model is the stdlib heuristic.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..core.contracts import Module
from .features import FEATURE_NAMES, FeatureVector
from .models import RiskModel

# Per-module baseline feature row (a "reference" sample). Attribution measures
# how moving each feature from this baseline to its observed value shifts the
# model's probability. Chosen as a benign/low-risk reference per driver.
_BASELINES: dict[Module, tuple[float, ...]] = {
    # low magnitude, far distance, resilient construction (rcc ordinal=2.0)
    Module.EARTHQUAKE: (4.0, 80.0, 2.0),
    # low rainfall, no surge, low river level
    Module.CYCLONE_FLOOD: (0.0, 0.0, 0.0),
    # low intensity, no wind, low fuel
    Module.FIRE_COLLAPSE: (0.0, 0.0, 0.0),
}


def baseline_row(module: Module) -> list[float]:
    """The reference feature row used as the SHAP/attribution baseline."""
    if module not in _BASELINES:
        raise ValueError(f"no baseline for module {module!r}")
    return list(_BASELINES[module])


@dataclass(frozen=True)
class Explanation:
    """An additive explanation of a single prediction (PRD Step 9).

    ``base_value`` is the model's probability at the baseline row; ``attributions``
    maps each feature name to a signed contribution; by construction
    ``base_value + sum(attributions.values()) ≈ prediction``. ``method`` records
    whether the real SHAP backend or the deterministic fallback produced it.
    """

    module: Module
    method: str
    base_value: float
    prediction: float
    attributions: dict[str, float]

    def as_dict(self) -> dict[str, float]:
        """Attribution dict in the shape agents log to ``log_prediction``."""
        return dict(self.attributions)

    def top_feature(self) -> str | None:
        """Name of the feature with the largest absolute attribution."""
        if not self.attributions:
            return None
        return max(self.attributions.items(), key=lambda kv: abs(kv[1]))[0]


def _values(fv: FeatureVector | Sequence[float]) -> list[float]:
    return fv.as_list() if isinstance(fv, FeatureVector) else [float(v) for v in fv]


def _fallback_attributions(
    model: RiskModel, values: Sequence[float]
) -> tuple[float, float, dict[str, float]]:
    """Deterministic marginal-contribution attribution against the baseline.

    For each feature, measure the change in predicted probability when that single
    feature moves from the baseline to its observed value (others held at observed
    values), then symmetrise with the reverse direction (others at baseline). The
    averaged contributions are rescaled so they sum exactly to ``pred - base``,
    yielding an additive SHAP-shaped explanation with NO external library.
    """
    names = FEATURE_NAMES[model.module]
    base = baseline_row(model.module)
    obs = list(values)

    base_pred = model.predict([base])[0]
    pred = model.predict([obs])[0]

    raw: dict[str, float] = {}
    for i, name in enumerate(names):
        # Direction 1: others at observed, toggle feature i down to baseline.
        row_down = list(obs)
        row_down[i] = base[i]
        d1 = pred - model.predict([row_down])[0]
        # Direction 2: others at baseline, toggle feature i up to observed.
        row_up = list(base)
        row_up[i] = obs[i]
        d2 = model.predict([row_up])[0] - base_pred
        raw[name] = 0.5 * (d1 + d2)

    gap = pred - base_pred
    total = sum(raw.values())
    if abs(total) > 1e-12:
        scale = gap / total
        attributions = {k: round(v * scale, 6) for k, v in raw.items()}
    else:
        # No marginal signal: distribute the gap evenly (still additive).
        share = gap / len(names) if names else 0.0
        attributions = {k: round(share, 6) for k in names}
    return round(base_pred, 6), round(pred, 6), attributions


def _shap_attributions(
    model: RiskModel, values: Sequence[float]
) -> tuple[float, float, dict[str, float]] | None:
    """Real SHAP values for one row, or ``None`` to signal "use fallback".

    Only attempted when ``shap`` imports AND the model exposes a fitted real
    backend object (the heuristic has none, so we skip straight to the fallback).
    """
    if getattr(model, "_backend_obj", None) is None:
        return None
    try:  # pragma: no cover - exercised only when shap + a backend are installed
        import numpy as np  # type: ignore
        import shap  # type: ignore
    except Exception:  # pragma: no cover
        return None
    try:  # pragma: no cover
        names = FEATURE_NAMES[model.module]
        base = np.asarray([baseline_row(model.module)], dtype=float)
        obs = np.asarray([list(values)], dtype=float)

        def _f(rows: Any) -> Any:
            return np.asarray(model.predict([list(r) for r in rows]), dtype=float)

        explainer = shap.Explainer(_f, base)
        result = explainer(obs)
        row_vals = np.asarray(result.values)[0]
        base_value = float(np.asarray(result.base_values).ravel()[0])
        pred = float(model.predict([list(values)])[0])
        attributions = {
            name: round(float(row_vals[i]), 6) for i, name in enumerate(names)
        }
        return round(base_value, 6), round(pred, 6), attributions
    except Exception:  # pragma: no cover
        return None


def explain(model: RiskModel, fv: FeatureVector | Sequence[float]) -> Explanation:
    """Explain ``model``'s prediction for one row (SHAP or stdlib fallback).

    Returns an :class:`Explanation` whose ``attributions`` dict matches the shape
    the tier-2 agents log to ``log_prediction`` (``{feature_name: float}``). Uses
    real SHAP when importable *and* the model has a fitted real backend; otherwise
    a deterministic, additive marginal-contribution fallback (PRD Step 10).
    """
    values = _values(fv)
    if len(values) != len(FEATURE_NAMES[model.module]):
        raise ValueError(
            f"expected {len(FEATURE_NAMES[model.module])} features for "
            f"module {model.module!r}, got {len(values)}"
        )

    real = _shap_attributions(model, values)
    if real is not None:  # pragma: no cover - needs shap + backend
        base_value, pred, attributions = real
        method = "shap"
    else:
        base_value, pred, attributions = _fallback_attributions(model, values)
        method = "fallback"

    return Explanation(
        module=model.module,
        method=method,
        base_value=base_value,
        prediction=pred,
        attributions=attributions,
    )


def explain_dict(model: RiskModel, fv: FeatureVector | Sequence[float]) -> dict[str, float]:
    """Convenience: just the attribution dict (the shape agents log)."""
    return explain(model, fv).as_dict()
