"""Real historical FLOOD dataset — leak-free, temporally split, real labels.

Source: the committed fixture built by ``python -m disastermind.ml.validation.
fetch flood`` — GloFAS-ERA5 river-discharge reanalysis + ERA5 daily
precipitation for 12 real Indian river-basin sites (Brahmaputra, Barak, Ganga,
Kosi, Mahanadi, Yamuna, Godavari, Krishna, Narmada), 2010-2023 daily. No
synthetic data anywhere in this path.

Methodology (mirrors the earthquake dataset's honesty rules):

  * FEATURES at day ``t`` use only information available at the END of day ``t``:
    trailing rainfall accumulations (1/3/7/30-day), today's discharge expressed
    as a percentile of the site's TRAIN-YEARS climatology, the 3-day discharge
    trend, and seasonality (day-of-year sine/cosine). Nothing from ``t+1``
    onward ever enters a feature.
  * LABEL is a real hydrological outcome: did discharge in the NEXT 1-3 days
    reach the site's flood threshold (95th percentile of TRAIN-period
    discharge)? The threshold is computed from training years only, so the
    test period cannot leak its own distribution into the event definition.
    ``severe`` (99th percentile) marks the rare-event tier for tail analysis.
  * SPLIT is TEMPORAL: train 2010-2018, test 2019-2023 — five fully
    out-of-sample monsoon seasons.
  * BASELINES are the operational incumbents, not straw men: **persistence**
    (today's discharge relative to the flood threshold — the standard
    hydrological no-model forecast) and **seasonal climatology** (train-years
    flood frequency for that day-of-year and site).

Each row also carries ``region`` (basin block for leave-one-region-out CV),
``setting`` (urban/rural — the fairness-audit equity axis), ``year`` (rolling-
origin CV) and ``severity`` (label-window peak discharge over the flood
threshold, for tail slices). Stdlib only; the fixture is the only input.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
from dataclasses import dataclass

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "openmeteo_glofas_india_2010_2023.json"
)

#: Leak-free feature names, in feature_row order. ``discharge_ratio`` is the
#: persistence baseline's own signal (today's discharge over the train-derived
#: flood threshold) fed to the model as a feature, so the incumbent is a floor
#: and the model learns the residual — still strictly day-t information.
FEATURE_NAMES = (
    "precip_1d",
    "precip_3d",
    "precip_7d",
    "precip_30d",
    "discharge_pctl",
    "discharge_ratio",
    "discharge_trend_3d",
    "doy_sin",
    "doy_cos",
)

#: Temporal split: train < 2019-01-01 <= test (five held-out monsoon seasons).
SPLIT_YEAR = 2019
#: Feature warm-up (the 30-day rain accumulation) and label look-ahead horizon.
WARMUP_DAYS = 30
HORIZON_DAYS = 3
#: Lead times (days ahead) for the lead-time-vs-POD curve. Point-in-time labels
#: ``discharge at day t+h >= flood threshold`` let the eval ask "how many days
#: of actionable warning do we give?" — a forecast that is accurate only at t+0
#: cannot drive an evacuation. ``MAX_HORIZON`` bounds the look-ahead window.
HORIZONS = (1, 2, 3, 5, 7)
MAX_HORIZON = max(HORIZONS)
#: Flood / severe-flood thresholds as train-climatology quantiles.
FLOOD_QUANTILE = 0.95
SEVERE_QUANTILE = 0.99


@dataclass(frozen=True)
class FloodRow:
    """One (site, day) validation row with features, labels and audit tags."""

    site: str
    basin: str
    region: str
    setting: str  # "urban" | "rural" — the fairness-audit axis
    date: _dt.date
    features: tuple[float, ...]
    label: int  # flood threshold reached within the next HORIZON_DAYS
    severe: int  # severe (q99) threshold reached within the horizon
    severity: float  # peak horizon discharge / flood threshold (tail payload)
    persistence: float  # operational baseline score: today's q / threshold
    climatology: float  # operational baseline score: train flood freq @ doy
    horizon_labels: tuple[int, ...]  # per-HORIZONS: discharge at day t+h >= flood_thr

    @property
    def year(self) -> int:
        return self.date.year

    def label_at(self, lead_days: int) -> int:
        """Point-in-time flood label ``lead_days`` ahead (for the lead-time curve)."""
        return self.horizon_labels[HORIZONS.index(lead_days)]


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile of a pre-sorted list (stdlib)."""
    if not sorted_values:
        raise ValueError("empty sample")
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _percentile_of(sorted_values: list[float], x: float) -> float:
    """Fraction of the (train) sample strictly below ``x`` — in [0, 1]."""
    lo, hi = 0, len(sorted_values)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_values)


def load_rows(path: str = FIXTURE) -> list[FloodRow]:
    """Build all validation rows from the committed real fixture (no network).

    Per site: derive train-climatology thresholds and day-of-year flood
    frequencies from TRAIN YEARS ONLY, then emit one row per day that has a
    full feature warm-up and a full label horizon. Days with gaps (None) in
    any window are skipped rather than imputed — honesty over row count.
    """
    with open(path, encoding="utf-8") as fh:
        fixture = json.load(fh)

    rows: list[FloodRow] = []
    for site in fixture["sites"]:
        start = _dt.date.fromisoformat(site["start"])
        discharge: list[float | None] = site["discharge"]
        precip: list[float | None] = site["precip"]
        n = len(discharge)
        dates = [start + _dt.timedelta(days=i) for i in range(n)]

        # --- train-years climatology (threshold + day-of-year flood frequency)
        train_q = sorted(
            q for q, d in zip(discharge, dates) if q is not None and d.year < SPLIT_YEAR
        )
        if len(train_q) < 365:
            continue  # site unusable; skip whole site visibly (row count drops)
        flood_thr = _quantile(train_q, FLOOD_QUANTILE)
        severe_thr = _quantile(train_q, SEVERE_QUANTILE)
        if flood_thr <= 0:
            continue
        # std of train discharge for the trend feature's scale
        mean_q = sum(train_q) / len(train_q)
        std_q = math.sqrt(sum((v - mean_q) ** 2 for v in train_q) / len(train_q)) or 1.0

        # day-of-year flood frequency over train years (the climatology baseline)
        doy_events: dict[int, int] = {}
        doy_counts: dict[int, int] = {}
        for i, d in enumerate(dates):
            if d.year >= SPLIT_YEAR or discharge[i] is None:
                continue
            doy = min(d.timetuple().tm_yday, 365)
            doy_counts[doy] = doy_counts.get(doy, 0) + 1
            if discharge[i] >= flood_thr:
                doy_events[doy] = doy_events.get(doy, 0) + 1
        base_rate = sum(doy_events.values()) / max(1, sum(doy_counts.values()))

        # --- per-day rows. Look-ahead spans MAX_HORIZON so each row carries both
        # the 1-3 day window label and the per-horizon point-in-time labels.
        for t in range(WARMUP_DAYS, n - MAX_HORIZON):
            window = discharge[t + 1 : t + 1 + HORIZON_DAYS]
            horizon_q = [discharge[t + h] for h in HORIZONS]
            feature_precip = precip[t - 29 : t + 1]
            if (
                discharge[t] is None
                or discharge[t - 3] is None
                or any(v is None for v in window)
                or any(v is None for v in horizon_q)
                or any(v is None for v in feature_precip)
            ):
                continue
            d = dates[t]
            doy = min(d.timetuple().tm_yday, 365)
            angle = 2.0 * math.pi * doy / 365.0
            p = feature_precip  # 30 entries, p[-1] is day t
            features = (
                float(p[-1]),
                float(sum(p[-3:])),
                float(sum(p[-7:])),
                float(sum(p)),
                _percentile_of(train_q, float(discharge[t])),
                float(discharge[t]) / flood_thr,
                (float(discharge[t]) - float(discharge[t - 3])) / std_q,
                math.sin(angle),
                math.cos(angle),
            )
            peak = max(float(v) for v in window)
            # climatology: smoothed +/-7-day train flood frequency at this doy
            num = den = 0
            for k in range(doy - 7, doy + 8):
                kk = ((k - 1) % 365) + 1
                num += doy_events.get(kk, 0)
                den += doy_counts.get(kk, 0)
            climatology = (num / den) if den else base_rate
            rows.append(
                FloodRow(
                    site=site["name"],
                    basin=site["basin"],
                    region=site["region"],
                    setting=site["setting"],
                    date=d,
                    features=features,
                    label=1 if peak >= flood_thr else 0,
                    severe=1 if peak >= severe_thr else 0,
                    severity=peak / flood_thr,
                    persistence=float(discharge[t]) / flood_thr,
                    climatology=climatology,
                    horizon_labels=tuple(
                        1 if float(q) >= flood_thr else 0 for q in horizon_q
                    ),
                )
            )
    return rows


def temporal_split(rows: list[FloodRow]) -> tuple[list[FloodRow], list[FloodRow]]:
    """Train = years < SPLIT_YEAR, test = years >= SPLIT_YEAR. No leakage."""
    train = [r for r in rows if r.year < SPLIT_YEAR]
    test = [r for r in rows if r.year >= SPLIT_YEAR]
    return train, test


def to_xy(rows: list[FloodRow]) -> tuple[list[list[float]], list[int]]:
    """Feature matrix and binary flood labels for a set of rows."""
    return [list(r.features) for r in rows], [r.label for r in rows]
