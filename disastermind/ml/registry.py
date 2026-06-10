"""Model registry — one risk-model wrapper per module (A / B / C).

PRD Step 3 ties each disaster module to a prediction model. This registry is the
single lookup the rest of the system uses to obtain *the* wrapper for a module,
so callers never hard-code which backend a module prefers:

  * Module A (cyclone/flood) -> XGBoost (tabular rainfall/surge/river drivers)
  * Module B (earthquake)    -> XGBoost (HAZUS-style fragility drivers)
  * Module C (urban fire)    -> scikit-learn logistic baseline (wind/density)

Every wrapper degrades to the deterministic stdlib heuristic when its optional
library is absent or unfitted (see :mod:`disastermind.ml.models`), so
``get_model(...).predict(...)`` ALWAYS returns probabilities in [0, 1] with no
optional dependency and no network — even on a bare stdlib install.

Wrappers are constructed lazily and cached per module so repeated lookups share a
single (potentially fitted) instance; ``reset_registry()`` clears the cache for
tests. ``load_model`` restores a persisted artefact and registers it.
"""
from __future__ import annotations

from ..core.contracts import Module
from .features import FEATURE_NAMES
from .models import (
    HeuristicRiskModel,
    RiskModel,
    SklearnRiskModel,
    XGBoostRiskModel,
)

#: Module -> preferred wrapper class (PRD Step 3 model assignment). Each falls
#: back to the heuristic internally, so this only picks the *attempted* backend.
DEFAULT_BACKENDS: dict[Module, type[RiskModel]] = {
    Module.CYCLONE_FLOOD: XGBoostRiskModel,
    Module.EARTHQUAKE: XGBoostRiskModel,
    Module.FIRE_COLLAPSE: SklearnRiskModel,
}

# Process-wide cache of constructed wrappers, keyed by module.
_CACHE: dict[Module, RiskModel] = {}


def _coerce_module(module: Module | str) -> Module:
    """Accept a :class:`Module`, its value ("A"/"B"/"C") or its name."""
    if isinstance(module, Module):
        return module
    text = str(module)
    try:
        return Module(text)
    except ValueError:
        try:
            return Module[text.upper()]
        except KeyError as exc:
            raise ValueError(f"unknown module {module!r}") from exc


def model_class_for(module: Module | str) -> type[RiskModel]:
    """Return the preferred wrapper class for ``module`` (no instantiation)."""
    m = _coerce_module(module)
    if m not in FEATURE_NAMES:
        raise ValueError(f"unsupported module {m!r}")
    return DEFAULT_BACKENDS.get(m, HeuristicRiskModel)


def get_model(module: Module | str, *, fresh: bool = False) -> RiskModel:
    """Return the cached :class:`RiskModel` wrapper for ``module`` (A/B/C).

    Constructs the module's preferred wrapper on first access and caches it. Pass
    ``fresh=True`` to bypass/replace the cache with a new instance. The returned
    wrapper always answers ``predict`` (heuristic fallback) regardless of whether
    its optional backend is installed.
    """
    m = _coerce_module(module)
    if not fresh and m in _CACHE:
        return _CACHE[m]
    model = model_class_for(m)(m)
    _CACHE[m] = model
    return model


def register_model(module: Module | str, model: RiskModel) -> RiskModel:
    """Install ``model`` as the wrapper for ``module`` (e.g. a fitted model).

    Validates that the model's module matches and that its feature schema is the
    canonical one, then caches it for subsequent :func:`get_model` calls.
    """
    m = _coerce_module(module)
    if model.module is not m:
        raise ValueError(f"model module {model.module!r} != {m!r}")
    if tuple(model.feature_names) != FEATURE_NAMES[m]:
        raise ValueError("model feature schema does not match module schema")
    _CACHE[m] = model
    return model


def load_model(module: Module | str, path: str) -> RiskModel:
    """Load a persisted artefact, register it for ``module`` and return it."""
    m = _coerce_module(module)
    model = RiskModel.load(path)
    return register_model(m, model)


def all_models() -> dict[Module, RiskModel]:
    """Return a freshly-cached wrapper for every supported module (A/B/C)."""
    return {m: get_model(m) for m in FEATURE_NAMES}


def reset_registry() -> None:
    """Clear the wrapper cache (test isolation)."""
    _CACHE.clear()
