"""Real-data training sets for the runtime risk models — no synthetic anywhere.

The production training pipeline (:func:`disastermind.ml.training.train_all`)
fits one :class:`~disastermind.ml.models.RiskModel` per module against the
runtime feature schemas in :data:`disastermind.ml.features.FEATURE_NAMES`. This
module derives those training tables from the SAME committed real fixtures the
validation suite scores against (USGS earthquakes, GloFAS/ERA5 floods,
FPA-FOD/ERA5 wildfires), replacing the synthetic generator in the production
path. :mod:`~disastermind.ml.training.synthetic` remains available for unit
tests that need a controllable signal, but no shipped artefact is fitted on it.

Schema mappings (each documented where it is an approximation, because the
runtime schemas predate the real datasets):

  * **Module B (earthquake)** ``(magnitude, distance_km, construction)``:
    magnitude is the real catalog magnitude; ``distance_km`` uses hypocentral
    depth — the minimum possible source-to-site distance, i.e. the conservative
    bound available catalog-wide; ``construction`` is the 'unknown' ordinal
    (0.5) because the global catalog carries no building stock. Label: the
    measured damage-grade outcome (ShakeMap MMI>=VI or PAGER>=yellow).
  * **Module A (cyclone/flood)** ``(rainfall_mm, storm_surge_m, river_level_m)``:
    rainfall is the real trailing 7-day ERA5 accumulation; storm surge is 0.0
    (river sites carry no surge gauge — stated, not faked); river level maps
    the site's train-climatology discharge percentile onto the 0-10 m stage
    scale the agents use. Label: the real flood outcome (q95 threshold reached
    within 3 days).
  * **Module C (fire)** ``(intensity, wind_speed_ms, base_fuel)``: intensity is
    the Angström fire-weather severity (4 - index, clamped to the agents' 0-5
    scale); wind converts ERA5 km/h to m/s; base fuel maps the 30-day dry-streak
    onto the 0-4 fuel-dryness ordinal. Label: real next-day wildfire occurrence.

Row capping is a deterministic stratified stride (positives and negatives
sampled proportionally, at least one positive kept) so a capped table never
collapses to a single class. Everything is reproducible: no RNG, no clock.
"""
from __future__ import annotations

from ...core.contracts import Module

# NOTE: the validation dataset modules are imported lazily inside
# make_real_dataset(). ml.training and ml.eval import each other's packages
# (backtest consumes the synthetic generator), so a module-level import of
# ml.validation here would close an import cycle at package-init time.


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _stratified_cap(
    X: list[list[float]], y: list[float], cap: int | None
) -> tuple[list[list[float]], list[float]]:
    """Deterministic stride subsample preserving the class mix (>=1 positive)."""
    if cap is None or len(X) <= cap:
        return X, y
    pos = [i for i, v in enumerate(y) if v >= 0.5]
    neg = [i for i, v in enumerate(y) if v < 0.5]
    n_pos = max(1, round(cap * len(pos) / len(X))) if pos else 0
    n_neg = cap - n_pos

    def _stride(idx: list[int], k: int) -> list[int]:
        if k <= 0 or not idx:
            return []
        if len(idx) <= k:
            return idx
        step = len(idx) / k
        return [idx[int(i * step)] for i in range(k)]

    chosen = sorted(_stride(pos, n_pos) + _stride(neg, n_neg))
    return [X[i] for i in chosen], [y[i] for i in chosen]


def make_real_dataset(
    module: Module, n: int | None = None
) -> tuple[list[list[float]], list[float]]:
    """``(X, y)`` for ``module`` from the committed REAL fixtures.

    ``X`` columns align with ``FEATURE_NAMES[module]``; ``y`` are real observed
    outcomes in {0.0, 1.0}. ``n`` caps the row count via the stratified stride
    (None = all rows). Deterministic for a given fixture set.
    """
    from ..validation import dataset as quake_ds
    from ..validation import fire as fire_ds
    from ..validation import flood as flood_ds

    if module is Module.EARTHQUAKE:
        quakes = quake_ds.load_quakes()
        X = [[q.mag, q.depth_km, 0.5] for q in quakes]
        y = [float(q.label_damaging()) for q in quakes]
    elif module is Module.CYCLONE_FLOOD:
        rows = flood_ds.load_rows()
        # features: (precip_1d, precip_3d, precip_7d, precip_30d, discharge_pctl, ...)
        X = [[r.features[2], 0.0, r.features[4] * 10.0] for r in rows]
        y = [float(r.label) for r in rows]
    elif module is Module.FIRE_COLLAPSE:
        rows = fire_ds.load_rows()
        # features: (tmax, rh_min, wind_max_kmh, days_since_rain, precip_30d, streak, ...)
        X = [
            [
                _clamp(r.angstrom_score, 0.0, 5.0),
                r.features[2] / 3.6,
                _clamp(r.features[5] / 30.0 * 4.0, 0.0, 4.0),
            ]
            for r in rows
        ]
        y = [float(r.label) for r in rows]
    else:
        raise ValueError(f"no real dataset mapping for module {module!r}")
    return _stratified_cap(X, y, n)
