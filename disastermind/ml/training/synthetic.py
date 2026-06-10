"""Deterministic synthetic training-data generators (PRD Step 10).

The prediction agents' ML seam loads a fitted :class:`disastermind.ml.RiskModel`
per module (A=cyclone/flood, B=earthquake, C=urban fire/collapse). Before such a
model can be persisted it must be *fit* on labelled tabular data whose columns
match :data:`disastermind.ml.features.FEATURE_NAMES` for that module. This module
manufactures that data **without any network, file or optional dependency** and
**fully deterministically**: given the same ``(module, n, seed)`` it always emits
byte-for-byte identical ``(X, y)``.

Design
------
* **Schema fidelity.** Each row of ``X`` is an ordered ``list[float]`` aligned
  positionally with ``FEATURE_NAMES[module]`` (so it flows straight into
  ``RiskModel.fit`` / ``.predict``). The per-feature sampling ranges below mirror
  the realistic driver magnitudes the tier-2 agents already work with (magnitude
  in MMI units, rainfall in mm, wind in m/s, ...).
* **Known monotonic signal.** Labels come from a fixed logistic over a per-module
  weight vector in which the *dominant hazard driver* (rainfall / magnitude /
  intensity) pushes risk **up** and the *resilience driver* (epicentral distance,
  construction class) pushes it **down**. So a higher-rainfall / higher-MMI /
  higher-intensity row is, all else equal, labelled at higher risk — the property
  the trained model must reproduce.
* **Seeded pseudo-noise.** A single :class:`random.Random` instance (seeded from
  ``seed`` mixed with a per-module salt) supplies both the feature draws and a
  small additive label jitter. Nothing reads the wall clock or the global RNG, so
  runs reproduce exactly.

The generator deliberately reuses the SAME monotone direction as
:data:`disastermind.ml.models._HEURISTICS` so that even the stdlib heuristic
fallback (no xgboost/sklearn) already ranks rows the way these labels do.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from ...core.contracts import Module
from ..features import FEATURE_NAMES


# --------------------------------------------------------------------------- math
def _logistic(x: float) -> float:
    """Numerically-safe logistic squash to (0, 1) (mirrors ml.models)."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


# --------------------------------------------------------------------------- spec
@dataclass(frozen=True)
class _FeatureSpec:
    """How one feature column is sampled and how strongly it drives the label.

    ``lo``/``hi`` bound the uniform draw for the column; ``scale`` normalises the
    raw value into a comparable range before the label weight applies; ``weight``
    is the (signed) pull of this driver on the latent risk score. A positive
    weight => "more of this raises risk"; negative => "more of this lowers risk".
    """

    lo: float
    hi: float
    scale: float
    weight: float


# Per-module column specs, ORDERED to match FEATURE_NAMES[module] exactly. The
# directions mirror disastermind.ml.models._HEURISTICS so the synthetic labels and
# the heuristic fallback agree on monotonicity.
_SPECS: dict[Module, tuple[_FeatureSpec, ...]] = {
    # ("magnitude", "distance_km", "construction"):
    #   magnitude up -> more shaking -> risk up;
    #   distance up  -> less shaking -> risk down;
    #   construction ordinal up (more resilient) -> risk down.
    Module.EARTHQUAKE: (
        _FeatureSpec(lo=3.0, hi=9.0, scale=9.0, weight=3.0),
        _FeatureSpec(lo=0.0, hi=200.0, scale=80.0, weight=-2.0),
        _FeatureSpec(lo=0.0, hi=2.0, scale=2.0, weight=-1.2),
    ),
    # ("rainfall_mm", "storm_surge_m", "river_level_m"): all push inundation up.
    Module.CYCLONE_FLOOD: (
        _FeatureSpec(lo=0.0, hi=500.0, scale=300.0, weight=2.2),
        _FeatureSpec(lo=0.0, hi=8.0, scale=6.0, weight=1.4),
        _FeatureSpec(lo=0.0, hi=12.0, scale=8.0, weight=1.2),
    ),
    # ("intensity", "wind_speed_ms", "base_fuel"): all push burn probability up.
    Module.FIRE_COLLAPSE: (
        _FeatureSpec(lo=0.0, hi=5.0, scale=3.0, weight=2.0),
        _FeatureSpec(lo=0.0, hi=30.0, scale=25.0, weight=1.3),
        _FeatureSpec(lo=0.0, hi=4.0, scale=3.0, weight=0.9),
    ),
}

# Per-module latent-score intercept (sets base rate) and label noise std-dev.
_INTERCEPT: dict[Module, float] = {
    Module.EARTHQUAKE: -1.6,
    Module.CYCLONE_FLOOD: -1.7,
    Module.FIRE_COLLAPSE: -1.4,
}
_NOISE_STD = 0.05

# Per-module salt so different modules drawn from the same ``seed`` are not
# correlated row-for-row. Fixed constants => still fully deterministic.
_SALT: dict[Module, int] = {
    Module.EARTHQUAKE: 0xB,
    Module.CYCLONE_FLOOD: 0xA,
    Module.FIRE_COLLAPSE: 0xC,
}


def _specs_for(module: Module) -> tuple[_FeatureSpec, ...]:
    specs = _SPECS.get(module)
    if specs is None:
        raise ValueError(f"no synthetic spec for module {module!r}")
    # Defensive: spec order must line up with the frozen feature schema.
    if len(specs) != len(FEATURE_NAMES[module]):
        raise ValueError(  # pragma: no cover - guards against schema drift
            f"spec/feature length mismatch for {module!r}"
        )
    return specs


def _rng_for(module: Module, seed: int) -> random.Random:
    """A dedicated, seeded RNG for ``module`` (never the global RNG / clock)."""
    return random.Random((int(seed) << 8) ^ _SALT[module])


# --------------------------------------------------------------------------- API
def make_dataset(
    module: Module,
    n: int = 256,
    seed: int = 0,
) -> tuple[list[list[float]], list[float]]:
    """Return ``(X, y)`` synthetic training data for ``module``.

    ``X`` is ``n`` rows, each an ordered ``list[float]`` matching
    ``FEATURE_NAMES[module]``; ``y`` is ``n`` risk labels in ``[0, 1]`` produced by
    a fixed monotone logistic over the row plus seeded jitter. Deterministic in
    ``(module, n, seed)``.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    specs = _specs_for(module)
    intercept = _INTERCEPT[module]
    rng = _rng_for(module, seed)

    X: list[list[float]] = []
    y: list[float] = []
    for _ in range(n):
        row: list[float] = []
        score = intercept
        for spec in specs:
            value = rng.uniform(spec.lo, spec.hi)
            row.append(value)
            score += spec.weight * (value / (spec.scale or 1.0))
        # Seeded additive jitter on the latent score keeps labels off the exact
        # logistic curve without breaking reproducibility.
        score += rng.gauss(0.0, 1.0) * _NOISE_STD
        X.append(row)
        y.append(_clamp01(_logistic(score)))
    return X, y


def label_for(module: Module, row: Sequence[float]) -> float:
    """Noise-free monotone label for one ``row`` (the underlying signal).

    Useful for tests / sanity checks: this is the exact logistic the dataset is
    built from, *without* the per-row jitter, so two rows can be compared on the
    pure monotonic signal alone.
    """
    specs = _specs_for(module)
    score = _INTERCEPT[module]
    for spec, value in zip(specs, row):
        score += spec.weight * (float(value) / (spec.scale or 1.0))
    return _clamp01(_logistic(score))


def extreme_rows(module: Module) -> tuple[list[float], list[float]]:
    """Return ``(low_risk_row, high_risk_row)`` per ``module``.

    The high-risk row maxes every hazard driver and minimises every resilience
    driver; the low-risk row does the opposite. Deterministic, dependency-free —
    handy for asserting a trained model ranks high signal above low signal.
    """
    specs = _specs_for(module)
    low: list[float] = []
    high: list[float] = []
    for spec in specs:
        if spec.weight >= 0:  # hazard driver: high value => high risk
            low.append(spec.lo)
            high.append(spec.hi)
        else:  # resilience driver: high value => low risk
            low.append(spec.hi)
            high.append(spec.lo)
    return low, high
