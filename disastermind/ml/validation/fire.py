"""Real historical WILDFIRE dataset — leak-free, temporally split, real labels.

Source: the committed fixture built by ``python -m disastermind.ml.validation.
fetch fire`` — ERA5 daily fire weather joined with REAL wildfire occurrences
from the USDA Forest Service FPA-FOD ("Karen Short") database for 12 Pacific-
Northwest cells, 2012-2018 daily. The public FPA-FOD layer fully covers OR+WA
for this window (verified server-side at fetch time); the study region is
therefore the PNW and says so. NASA FIRMS is the intended primary for Indian
fire detections when that host is reachable; provenance is recorded in the
fixture. No synthetic data anywhere in this path.

Methodology:

  * FEATURES at day ``t`` are fire-weather drivers known at the end of day
    ``t``: max temperature, min relative humidity, max wind, days since rain,
    trailing 30-day precipitation (drought proxy), a consecutive-dry-day streak,
    and seasonality. Nothing after day ``t`` enters a feature.
  * LABEL is a real outcome: was at least one wildfire DISCOVERED in this cell
    on day ``t+1`` (agency-reported ignition, not a satellite artefact)?
    ``severity`` carries the largest fire size (acres) in the label window for
    tail slices (100+/1000+ acre events).
  * SPLIT is TEMPORAL: train 2012-2016, test 2017-2018 — two fully held-out
    fire seasons (2017 was a record PNW season: a genuine stress test, not a
    benign hold-out).
  * BASELINE is the operational incumbent formula, not a straw man: the
    **Angström fire-danger index** ``I = R/20 + (27 - T)/10`` (R = relative
    humidity %, T = max temperature degC), in use by fire services since the
    1940s; ``I < 2.5`` is the classic "fire weather likely" alarm. We expose
    ``angstrom_score = 4 - I`` so that HIGHER = riskier, comparable on AUC with
    the model, and 1.5 (= I at 2.5) is its operational alarm threshold.

Rows carry ``region`` (5 PNW fire-regime blocks for leave-one-region-out CV)
and ``year`` (rolling-origin CV). Stdlib only; the fixture is the only input.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
from dataclasses import dataclass

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "fpafod_era5_fire_2012_2018.json"
)

#: Leak-free feature names, in feature order.
FEATURE_NAMES = (
    "tmax_c",
    "rh_min_pct",
    "wind_max_kmh",
    "days_since_rain",
    "precip_30d",
    "dry_streak_30d_cap",
    "doy_sin",
    "doy_cos",
)

#: Temporal split: train < 2017 <= test (two held-out fire seasons).
SPLIT_YEAR = 2017
#: Feature warm-up for the 30-day accumulations.
WARMUP_DAYS = 30
#: Rain day definition (>= 2.6 mm, the standard "wetting rain" cutoff).
RAIN_MM = 2.6
#: Lead times (days ahead) for the lead-time-vs-POD curve. Fire ignition is more
#: stochastic than river flow, but fire WEATHER persists, so 1-3 day warning is
#: meaningful; ``label_at(h) = a fire is discovered on day t+h``.
HORIZONS = (1, 2, 3)
MAX_HORIZON = max(HORIZONS)


@dataclass(frozen=True)
class FireRow:
    """One (cell, day) validation row with features, label and audit tags."""

    cell: str
    state: str
    region: str
    date: _dt.date
    features: tuple[float, ...]
    label: int  # >=1 real wildfire discovered in the cell on day t+1
    severity: float  # largest fire (acres) discovered in the label window
    angstrom_score: float  # operational baseline, higher = riskier
    horizon_labels: tuple[int, ...]  # per-HORIZONS: a fire discovered on day t+h

    @property
    def year(self) -> int:
        return self.date.year

    def label_at(self, lead_days: int) -> int:
        """Point-in-time fire label ``lead_days`` ahead (for the lead-time curve)."""
        return self.horizon_labels[HORIZONS.index(lead_days)]


def angstrom_index(tmax_c: float, rh_min_pct: float) -> float:
    """The classic Angström fire-danger index (lower = more dangerous)."""
    return rh_min_pct / 20.0 + (27.0 - tmax_c) / 10.0


def load_rows(path: str = FIXTURE) -> list[FireRow]:
    """Build all validation rows from the committed real fixture (no network).

    Per cell: index the real fire discoveries by day, then emit one row per day
    with a full warm-up window and a next-day label. Days with weather gaps are
    skipped, not imputed.
    """
    with open(path, encoding="utf-8") as fh:
        fixture = json.load(fh)

    rows: list[FireRow] = []
    for cell in fixture["cells"]:
        start = _dt.date.fromisoformat(cell["start"])
        tmax, precip = cell["tmax"], cell["precip"]
        wind, rh = cell["wind_max"], cell["rh_min"]
        n = len(tmax)
        dates = [start + _dt.timedelta(days=i) for i in range(n)]
        index_of = {d: i for i, d in enumerate(dates)}

        # real fires by day index: count and largest size
        fire_count = [0] * n
        fire_size = [0.0] * n
        for f in cell["fires"]:
            try:
                day = index_of[_dt.date.fromisoformat(f["date"])]
            except (KeyError, ValueError):
                continue  # fire outside the weather window
            fire_count[day] += 1
            fire_size[day] = max(fire_size[day], float(f["size_acres"]))

        for t in range(WARMUP_DAYS, n - MAX_HORIZON):
            window_weather = [tmax[t], rh[t], wind[t]]
            p30 = precip[t - 29 : t + 1]
            if any(v is None for v in window_weather) or any(v is None for v in p30):
                continue
            # days since last wetting rain (capped at 60 for scale sanity)
            since = 60
            for back in range(min(60, t + 1)):
                v = precip[t - back]
                if v is not None and float(v) >= RAIN_MM:
                    since = back
                    break
            # longest dry streak within the trailing 30 days
            streak = best = 0
            for v in p30:
                if v is not None and float(v) < RAIN_MM:
                    streak += 1
                    best = max(best, streak)
                else:
                    streak = 0
            d = dates[t]
            doy = min(d.timetuple().tm_yday, 365)
            angle = 2.0 * math.pi * doy / 365.0
            features = (
                float(tmax[t]),
                float(rh[t]),
                float(wind[t]),
                float(since),
                float(sum(float(v) for v in p30)),
                float(best),
                math.sin(angle),
                math.cos(angle),
            )
            rows.append(
                FireRow(
                    cell=cell["name"],
                    state=cell["state"],
                    region=cell["region"],
                    date=d,
                    features=features,
                    label=1 if fire_count[t + 1] > 0 else 0,
                    severity=fire_size[t + 1],
                    angstrom_score=4.0 - angstrom_index(float(tmax[t]), float(rh[t])),
                    horizon_labels=tuple(
                        1 if fire_count[t + h] > 0 else 0 for h in HORIZONS
                    ),
                )
            )
    return rows


def temporal_split(rows: list[FireRow]) -> tuple[list[FireRow], list[FireRow]]:
    """Train = years < SPLIT_YEAR, test = years >= SPLIT_YEAR. No leakage."""
    train = [r for r in rows if r.year < SPLIT_YEAR]
    test = [r for r in rows if r.year >= SPLIT_YEAR]
    return train, test


def to_xy(rows: list[FireRow]) -> tuple[list[list[float]], list[int]]:
    """Feature matrix and binary next-day-fire labels for a set of rows."""
    return [list(r.features) for r in rows], [r.label for r in rows]
