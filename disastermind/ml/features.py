"""Feature extraction — domain inputs -> ordered numeric feature vectors.

PRD Step 3 (Prediction & Assessment): the real model layer behind the tier-2
prediction heuristics consumes tabular feature vectors. This module is the single
place that defines *which* drivers feed each module's model and *in what order*,
so :mod:`disastermind.ml.models`, :mod:`disastermind.ml.shap_explain` and the
prediction agents all agree on the schema without hard-coding magic indices.

Each module exposes a frozen, ordered list of feature names that mirrors the SHAP
attribution keys the tier-2 agents already log via
:meth:`DecisionLogger.log_prediction`:

  * Module B (earthquake):  ``magnitude``, ``distance_km``, ``construction``
        (mirrors ``tier2.prediction.agents.EarthquakeImpactAgent`` SHAP keys).
  * Module A (cyclone/flood): ``rainfall_mm``, ``storm_surge_m``, ``river_level_m``
        (mirrors ``CyclonePredictionAgent`` SHAP keys).
  * Module C (urban fire):  ``intensity``, ``wind_speed_ms``, ``base_fuel``
        (mirrors ``FireSpreadAgent`` SHAP keys).

Stdlib only: feature vectors are plain ``list[float]`` so nothing here requires
numpy. A :class:`FeatureVector` carries the values *and* their names so callers
can build name->value dicts (the shape SHAP wants) without re-deriving order.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..core.contracts import Module
from ..models.domain import EventKind
from ..models.geo import LatLon

# --------------------------------------------------------------------------- schema
#: Ordered feature names per module. These are FROZEN: models persist artefacts
#: keyed by this order and SHAP attributions key by these names, so appending is
#: safe but reordering is not.
FEATURE_NAMES: dict[Module, tuple[str, ...]] = {
    Module.EARTHQUAKE: ("magnitude", "distance_km", "construction"),
    Module.CYCLONE_FLOOD: ("rainfall_mm", "storm_surge_m", "river_level_m"),
    Module.FIRE_COLLAPSE: ("intensity", "wind_speed_ms", "base_fuel"),
}

#: Construction-class ordinal encoding (more resilient => higher value), so the
#: earthquake model sees a monotone "resilience" axis. Mirrors the fragility
#: ordering kutcha < pucca < rcc in ``tier2.prediction.agents.FRAGILITY``.
CONSTRUCTION_ORDINAL: dict[str, float] = {
    "kutcha": 0.0,
    "unknown": 0.5,
    "pucca": 1.0,
    "rcc": 2.0,
}


class FeatureError(ValueError):
    """Raised when an input cannot be coerced into the module's feature schema."""


@dataclass(frozen=True)
class FeatureVector:
    """A named, ordered numeric feature vector for one prediction instance.

    ``values`` aligns positionally with ``names`` (= ``FEATURE_NAMES[module]``).
    The pair lets callers build either a plain vector (for a model) or a
    name->value dict (for SHAP / audit) from one object.
    """

    module: Module
    names: tuple[str, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.names) != len(self.values):
            raise FeatureError(
                f"names/values length mismatch: {len(self.names)} != {len(self.values)}"
            )

    @property
    def dim(self) -> int:
        return len(self.values)

    def as_dict(self) -> dict[str, float]:
        """name -> value mapping (the shape SHAP attributions use)."""
        return {n: v for n, v in zip(self.names, self.values)}

    def as_list(self) -> list[float]:
        return list(self.values)


# --------------------------------------------------------------------------- coercion
def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_latlon(obj: Any) -> LatLon | None:
    if isinstance(obj, LatLon):
        return obj
    if isinstance(obj, dict) and "lat" in obj and "lon" in obj:
        return LatLon(_to_float(obj["lat"]), _to_float(obj["lon"]))
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        return LatLon(_to_float(obj[0]), _to_float(obj[1]))
    return None


def _construction_ordinal(value: Any) -> float:
    name = str(value or "unknown").strip().lower()
    return CONSTRUCTION_ORDINAL.get(name, CONSTRUCTION_ORDINAL["unknown"])


# --------------------------------------------------------------------------- builders
def quake_features(
    magnitude: float,
    distance_km: float,
    construction: str = "unknown",
) -> FeatureVector:
    """Module B feature vector: MMI driver (magnitude) + building stock.

    PRD Step 3 Module B (HAZUS-style fragility): collapse probability is driven
    by shaking intensity (here: magnitude attenuated by hypocentral distance) and
    the building's construction class. ``construction`` is ordinal-encoded.
    """
    return FeatureVector(
        module=Module.EARTHQUAKE,
        names=FEATURE_NAMES[Module.EARTHQUAKE],
        values=(
            _to_float(magnitude),
            _to_float(distance_km),
            _construction_ordinal(construction),
        ),
    )


def flood_features(
    rainfall_mm: float,
    storm_surge_m: float = 0.0,
    river_level_m: float = 0.0,
) -> FeatureVector:
    """Module A feature vector: rainfall + gauge (surge / river level).

    PRD Step 3 Module A (inundation forecast): per-cell inundation probability is
    driven by accumulated rainfall, coastal storm surge and river-gauge level.
    """
    return FeatureVector(
        module=Module.CYCLONE_FLOOD,
        names=FEATURE_NAMES[Module.CYCLONE_FLOOD],
        values=(
            _to_float(rainfall_mm),
            _to_float(storm_surge_m),
            _to_float(river_level_m),
        ),
    )


def fire_features(
    intensity: float,
    wind_speed_ms: float = 0.0,
    base_fuel: float = 1.0,
) -> FeatureVector:
    """Module C feature vector: wind + density (fuel) + ignition intensity.

    PRD Step 3 Module C (cellular-automata spread): rate-of-spread is driven by
    fire intensity, wind speed and the surrounding fuel/built density. ``base_fuel``
    proxies built-up density (more structures => more fuel).
    """
    return FeatureVector(
        module=Module.FIRE_COLLAPSE,
        names=FEATURE_NAMES[Module.FIRE_COLLAPSE],
        values=(
            _to_float(intensity),
            _to_float(wind_speed_ms),
            _to_float(base_fuel, default=1.0),
        ),
    )


# --------------------------------------------------------------- event/dict adapters
def features_for_module(module: Module, raw: dict[str, Any]) -> FeatureVector:
    """Build a module's :class:`FeatureVector` from a loose driver ``dict``.

    Accepts the same driver keys the tier-2 prediction agents compute (rainfall_mm,
    storm_surge_m, magnitude, wind_speed_ms, ...) plus a few synonyms, so an agent
    could adopt this extractor without reshaping its inputs. Missing drivers fall
    back to schema defaults rather than raising.
    """
    if module is Module.EARTHQUAKE:
        return quake_features(
            magnitude=raw.get("magnitude", raw.get("severity", 0.0)),
            distance_km=raw.get("distance_km", 0.0),
            construction=raw.get("construction", "unknown"),
        )
    if module is Module.CYCLONE_FLOOD:
        return flood_features(
            rainfall_mm=raw.get("rainfall_mm", 0.0),
            storm_surge_m=raw.get("storm_surge_m", raw.get("surge_m", 0.0)),
            river_level_m=raw.get("river_level_m", raw.get("level_m", 0.0)),
        )
    if module is Module.FIRE_COLLAPSE:
        return fire_features(
            intensity=raw.get("intensity", raw.get("severity", 0.0)),
            wind_speed_ms=raw.get("wind_speed_ms", raw.get("speed_ms", 0.0)),
            base_fuel=raw.get("base_fuel", raw.get("density", 1.0)),
        )
    raise FeatureError(f"no feature schema for module {module!r}")


def features_for_event(event: dict[str, Any], module: Module | None = None) -> FeatureVector:
    """Extract a feature vector from a :class:`DisasterEvent`-style dict.

    Reads ``severity`` + ``meta`` exactly as the tier-2 agents do, so a feed event
    flows straight into the model layer. ``module`` is inferred from ``kind`` when
    not given. Distance defaults to 0 km (epicentre-local) for the quake schema.
    """
    kind = str(event.get("kind", "")).lower()
    meta = event.get("meta", {}) or {}
    severity = _to_float(event.get("severity", 0.0))
    module = module or _module_for_kind(kind)

    if module is Module.EARTHQUAKE:
        return quake_features(
            magnitude=severity,
            distance_km=_to_float(meta.get("distance_km", 0.0)),
            construction=meta.get("construction", "unknown"),
        )
    if module is Module.CYCLONE_FLOOD:
        return flood_features(
            rainfall_mm=_to_float(meta.get("rainfall_mm", 50.0 + 30.0 * severity)),
            storm_surge_m=_to_float(meta.get("storm_surge_m", max(0.0, severity - 1.0))),
            river_level_m=_to_float(meta.get("river_level_m", 2.0 + 0.6 * severity)),
        )
    if module is Module.FIRE_COLLAPSE:
        return fire_features(
            intensity=severity,
            wind_speed_ms=_to_float(meta.get("wind_speed_ms", 3.0)),
            base_fuel=_to_float(meta.get("base_fuel", meta.get("density", 1.0)), default=1.0),
        )
    raise FeatureError(f"cannot map event kind {kind!r} to a feature schema")


def _module_for_kind(kind: str) -> Module:
    if kind == EventKind.EARTHQUAKE.value:
        return Module.EARTHQUAKE
    if kind in (EventKind.CYCLONE.value, EventKind.FLOOD.value):
        return Module.CYCLONE_FLOOD
    if kind in (EventKind.URBAN_FIRE.value, EventKind.STRUCTURAL_COLLAPSE.value):
        return Module.FIRE_COLLAPSE
    raise FeatureError(f"unknown event kind {kind!r}")


def feature_matrix(module: Module, rows: Iterable[dict[str, Any]]) -> list[list[float]]:
    """Build a 2-D feature matrix (list-of-rows) for batch fit()/predict().

    Stdlib representation of the X matrix; the model wrappers consume this and,
    when numpy is available, convert it lazily.
    """
    return [features_for_module(module, r).as_list() for r in rows]
