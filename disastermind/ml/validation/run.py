"""Validate ALL THREE hazard models on REAL historical data, end to end.

This is the orchestrator that turns the committed real fixtures (USGS quakes,
GloFAS/ERA5 floods, FPA-FOD/ERA5 wildfires) into one auditable report per
hazard, each containing every piece of evidence a deployment review needs:

  1. **Headline skill** on a strictly later out-of-sample test set (AUC, Brier,
     ECE) — never a random shuffle, never synthetic.
  2. **Operational-baseline comparisons with significance**: paired-bootstrap
     p-values against the incumbent a forecaster could use today (PAGER /
     GMPE attenuation for quakes, persistence + seasonal climatology for
     floods, the Angström fire-danger index for fire). "Better than no-skill"
     is not the bar; these are.
  3. **Decision-point metrics**: POD / FAR / CSI at an operating threshold
     chosen on a CALIBRATION split (never on test) for a target POD, plus the
     explicit miss-vs-false-alarm cost trade.
  4. **Calibrated uncertainty**: isotonic recalibration (fit on the calibration
     split) with before/after ECE measured on test, and split-conformal
     prediction sets with verified empirical coverage.
  5. **Blocked generalisation**: leave-one-region-out and rolling-origin CV —
     worst-block AUC reported next to the mean, because "works on average"
     is not "works in the held-out basin".
  6. **Fairness audit** per declared equity axis (urban/rural sites, regions,
     magnitude bands) at the shared operational threshold.
  7. **Rare-severe tail**: POD and severe-vs-rest discrimination on the worst
     events with bootstrap CIs.
  8. **Drift + retraining**: PSI/KS feature drift train->test and an explicit
     retrain trigger fed by the rolling-origin decay curve.

The model under test is the same deterministic stdlib logistic fit used since
the first earthquake validation (standardised batch gradient descent) — no
optional dependencies, no network, fully reproducible. Run:
``python -m disastermind.ml.validation`` (add ``--hazard`` / ``--json``).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..eval.conformal import (
    calibration_report,
    coverage_report,
    fit_conformal,
    fit_isotonic,
)
from ..eval.crossval import leave_one_region_out, rolling_origin, summarise
from ..eval.decision import confusion_at, operating_point_for_pod, operating_point_min_cost
from ..eval.drift import feature_drift, retrain_decision
from ..eval.fairness import audit_subgroups, equalized_thresholds, remediate
from ..eval.leadtime import lead_time_curve
from ..eval.leadtime import to_dict as leadtime_to_dict
from ..eval.metrics import (
    brier_score,
    calibration_bins,
    expected_calibration_error,
    roc_auc,
)
from ..eval.robustness import degradation_curve
from ..eval.robustness import to_dict as robustness_to_dict
from ..eval.significance import compare_auc, compare_brier
from ..eval.tail import SeveritySlice, tail_report
from . import fire as fire_ds
from . import flood as flood_ds
from .dataset import FEATURE_NAMES, load_quakes, temporal_split

__all__ = [
    "FitResult",
    "fit_logistic",
    "predict",
    "HazardSpec",
    "evaluate_hazard",
    "quake_spec",
    "flood_spec",
    "fire_spec",
    "quake_felt_vs_pager",
    "run_validation",
    "to_markdown",
    "HAZARDS",
]


# --------------------------------------------------------------- logistic (stdlib)
@dataclass
class FitResult:
    name: str
    weights: list[float]
    bias: float
    means: list[float]
    stds: list[float]


def _standardise(X: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    n, d = len(X), len(X[0])
    means = [sum(row[j] for row in X) / n for j in range(d)]
    stds = []
    for j in range(d):
        var = sum((row[j] - means[j]) ** 2 for row in X) / max(1, n)
        stds.append(math.sqrt(var) or 1.0)
    Z = [[(row[j] - means[j]) / stds[j] for j in range(d)] for row in X]
    return Z, means, stds


def _sigmoid(z: float) -> float:
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def fit_logistic(
    X: list[list[float]],
    y: list[int],
    *,
    name: str,
    epochs: int = 300,
    lr: float = 0.3,
    balanced: bool = False,
) -> FitResult:
    """Deterministic standardised-batch-gradient-descent logistic fit (stdlib).

    ``balanced=True`` weights each class inversely to its frequency (the
    standard rare-event treatment): with a 1-2% positive rate, unweighted batch
    GD crawls toward the optimum and underfits even its strongest feature. The
    weighting distorts the raw probability SCALE (recall is bought with a
    higher base score), which is exactly what the isotonic recalibration step
    downstream repairs; RANKING (AUC) is what this option improves.
    """
    Z, means, stds = _standardise(X)
    n, d = len(Z), len(Z[0])
    n_pos = sum(1 for v in y if v)
    wp = wn = 1.0
    if balanced and 0 < n_pos < n:
        wp = n / (2.0 * n_pos)
        wn = n / (2.0 * (n - n_pos))
    w = [0.0] * d
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for i in range(n):
            p = _sigmoid(sum(w[j] * Z[i][j] for j in range(d)) + b)
            err = (p - y[i]) * (wp if y[i] else wn)
            for j in range(d):
                gw[j] += err * Z[i][j]
            gb += err
        w = [w[j] - lr * gw[j] / n for j in range(d)]
        b -= lr * gb / n
    return FitResult(name=name, weights=w, bias=b, means=means, stds=stds)


def predict(fit: FitResult, X: list[list[float]]) -> list[float]:
    out = []
    for row in X:
        z = fit.bias + sum(
            fit.weights[j] * (row[j] - fit.means[j]) / fit.stds[j] for j in range(len(row))
        )
        out.append(_sigmoid(z))
    return out


def _metrics(y: list[int], p: list[float]) -> dict:
    bins = calibration_bins(y, p, n_bins=10)
    return {
        "auc": round(roc_auc(y, p), 4),
        "brier": round(brier_score(y, p), 4),
        "ece": round(expected_calibration_error(bins), 4),
        "n": len(y),
        "positives": sum(y),
        "reliability": [
            {"bin": round(b.mean_pred, 3), "observed": round(b.observed, 3), "count": b.count}
            for b in bins
            if b.count
        ],
    }


def _cap(rows: list, cap: int) -> list:
    """Deterministic stride subsample to at most ``cap`` rows (keeps time spread)."""
    if len(rows) <= cap:
        return rows
    step = len(rows) / cap
    return [rows[int(i * step)] for i in range(cap)]


# ------------------------------------------------------------------- hazard inputs
@dataclass
class HazardSpec:
    """Everything the generic evaluation engine needs for one hazard."""

    name: str
    source: str
    label_desc: str
    split_desc: str
    feature_names: tuple[str, ...]
    Xtr: list[list[float]]
    ytr: list[int]
    Xte: list[list[float]]
    yte: list[int]
    #: Fixed-formula operational baseline scores per TEST row (no fitting).
    baselines_test: dict[str, list[float]] = field(default_factory=dict)
    #: Equity axes: axis name -> group key per TEST row.
    fairness_axes_test: dict[str, list[str]] = field(default_factory=dict)
    #: Same axes per TRAIN row — used to fit per-group remediation thresholds on
    #: the calibration split (never on test). Optional; remediation is skipped
    #: for an axis absent here.
    fairness_axes_train: dict[str, list[str]] = field(default_factory=dict)
    #: Per-test-row severity payload + the slices to stratify on.
    severity_test: list[Any] = field(default_factory=list)
    slices: list[SeveritySlice] = field(default_factory=list)
    #: Full-catalog views for blocked CV (train + test, refit per fold).
    X_all: list[list[float]] = field(default_factory=list)
    y_all: list[int] = field(default_factory=list)
    regions_all: list[str] = field(default_factory=list)
    years_all: list[int] = field(default_factory=list)
    #: Per-horizon point-in-time labels for the lead-time curve (empty = N/A,
    #: e.g. earthquakes, which are instantaneous). Columns align with lead_hours.
    horizon_labels_train: list[tuple[int, ...]] = field(default_factory=list)
    horizon_labels_test: list[tuple[int, ...]] = field(default_factory=list)
    lead_hours: tuple[int, ...] = ()
    #: Per-test-row dates (date objects) for the GDACS external outcome check;
    #: set only for the flood hazard (GDACS India flood events are dated).
    dates_test: list = field(default_factory=list)
    target_pod: float = 0.90
    miss_cost: float = 100.0
    false_alarm_cost: float = 1.0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- engine
def evaluate_hazard(
    spec: HazardSpec,
    *,
    epochs: int = 150,
    cv_epochs: int = 60,
    fit_cap: int = 12000,
    cv_cap: int = 6000,
    n_boot: int = 250,
    tail_boot: int = 200,
    seed: int = 0,
) -> dict[str, Any]:
    """Run the full validation battery for one hazard; return the report section.

    Split discipline: the operating threshold and both calibrators are fitted on
    a CALIBRATION subset of train (every 5th row, ~20%); the model is fitted on
    the remainder; the TEST split is touched exactly once per artefact, to
    measure. CV folds use the full catalog and are reported separately from the
    headline (they re-fit per fold).
    """
    # --- calibration split (deterministic stride; all strictly pre-test rows)
    cal_idx = set(range(0, len(spec.ytr), 5))
    fit_rows = [(x, y) for i, (x, y) in enumerate(zip(spec.Xtr, spec.ytr, strict=False)) if i not in cal_idx]
    cal_rows = [(x, y) for i, (x, y) in enumerate(zip(spec.Xtr, spec.ytr, strict=False)) if i in cal_idx]
    fit_rows = _cap(fit_rows, fit_cap)
    X_fit, y_fit = [r[0] for r in fit_rows], [r[1] for r in fit_rows]
    X_cal, y_cal = [r[0] for r in cal_rows], [r[1] for r in cal_rows]

    model = fit_logistic(X_fit, y_fit, name=spec.name, epochs=epochs, balanced=True)
    p_cal_raw = predict(model, X_cal)
    p_te_raw = predict(model, spec.Xte)

    # --- calibrated uncertainty (fit on calibration, verified on test)
    iso = fit_isotonic(y_cal, p_cal_raw)
    p_cal = iso.transform(p_cal_raw)
    p_te = iso.transform(p_te_raw)
    calibration = calibration_report(spec.yte, p_te_raw, p_te)
    conformal = coverage_report(
        fit_conformal(y_cal, p_cal_raw, alpha=0.1), spec.yte, p_te_raw
    )

    # --- operating point chosen on calibration, evaluated on test
    op = operating_point_for_pod(y_cal, p_cal, spec.target_pod)
    threshold = op.threshold
    cost_op, _ = operating_point_min_cost(
        y_cal, p_cal, miss_cost=spec.miss_cost, false_alarm_cost=spec.false_alarm_cost
    )
    at_pod = confusion_at(spec.yte, p_te, threshold)
    at_cost = confusion_at(spec.yte, p_te, cost_op.threshold)
    decision = {
        "target_pod": spec.target_pod,
        "threshold_from_calibration": threshold,
        "test_at_target_pod": at_pod.to_dict(),
        "cost_assumptions": {"miss": spec.miss_cost, "false_alarm": spec.false_alarm_cost},
        "test_at_min_cost": at_cost.to_dict(),
        "test_cost_total": at_cost.fn * spec.miss_cost + at_cost.fp * spec.false_alarm_cost,
    }

    # --- operational baselines with paired-bootstrap significance.
    # AUC is compared on RAW model scores (AUC is calibration-invariant and
    # isotonic step-ties would only blur ranking); Brier is compared on the
    # calibrated probabilities, and only when the baseline itself is a
    # probability (an unbounded score has no meaningful Brier).
    comparisons: dict[str, Any] = {}
    for bname, p_base in spec.baselines_test.items():
        entry: dict[str, Any] = {
            "auc": compare_auc(spec.yte, p_te_raw, p_base, n_boot=n_boot, seed=seed).to_dict()
        }
        if all(0.0 <= v <= 1.0 for v in p_base):
            entry["brier"] = compare_brier(
                spec.yte, p_te, p_base, n_boot=n_boot, seed=seed
            ).to_dict()
        comparisons[bname] = entry

    # --- fairness audit at the shared operational threshold, with the
    # equalized-odds remediation (per-group thresholds fitted on the CALIBRATION
    # split, applied on test) when train-side axes are available.
    cal_order = sorted(cal_idx)
    fairness = {}
    for axis, groups in spec.fairness_axes_test.items():
        audit = audit_subgroups(spec.yte, p_te, groups, threshold=threshold)
        train_axis = spec.fairness_axes_train.get(axis)
        if train_axis and audit["under_protected_groups"]:
            cal_groups = [train_axis[i] for i in cal_order]
            # Fit per-group thresholds with headroom above the flag bar so the
            # calibration->test generalisation gap doesn't silently reopen it
            # (conservative calibration is standard hydrological practice).
            gthr = equalized_thresholds(
                y_cal, p_cal, cal_groups,
                target_pod=min(0.98, spec.target_pod + 0.05), fallback=threshold,
            )
            audit["remediation"] = remediate(
                spec.yte, p_te, groups,
                threshold=threshold, target_pod=spec.target_pod, group_thresholds=gthr,
            )
        fairness[axis] = audit

    # --- rare-severe tail
    tail = (
        tail_report(
            spec.yte,
            p_te,
            spec.severity_test,
            spec.slices,
            threshold=threshold,
            n_boot=tail_boot,
            seed=seed,
        )
        if spec.slices
        else None
    )

    # --- blocked CV (full catalog, per-fold refits)
    def factory(
        Xf: list[list[float]], yf: list[int]
    ) -> Callable[[list[list[float]]], list[float]]:
        rows = _cap(list(zip(Xf, yf, strict=False)), cv_cap)
        m = fit_logistic(
            [r[0] for r in rows], [r[1] for r in rows], name="cv", epochs=cv_epochs,
            balanced=True,
        )
        return lambda Xq: predict(m, Xq)

    loro_folds = leave_one_region_out(spec.X_all, spec.y_all, spec.regions_all, factory)
    rolling_folds = rolling_origin(spec.X_all, spec.y_all, spec.years_all, factory)

    # --- drift + retraining trigger
    drifts = feature_drift(spec.feature_names, spec.Xtr, spec.Xte)
    retrain = retrain_decision(drifts, rolling_folds)

    # --- degraded-input robustness (the FIXED model under sensor failure)
    robustness = robustness_to_dict(
        degradation_curve(
            lambda Xq: predict(model, Xq),
            X_fit,
            spec.Xte,
            spec.yte,
            target_pod=spec.target_pod,
            seed=seed,
        )
    )

    # --- external survey-grade outcome cross-check (GDACS declared floods)
    external = None
    if spec.dates_test:
        import collections

        from . import external as external_mod

        risk_by_date: dict = collections.defaultdict(float)
        for d, p in zip(spec.dates_test, p_te, strict=False):
            risk_by_date[d] = max(risk_by_date[d], p)
        ext_dates = sorted(risk_by_date)
        try:
            external = external_mod.cross_check_flood(
                ext_dates, [risk_by_date[d] for d in ext_dates], external_mod.load_gdacs()
            )
        except (FileNotFoundError, ValueError):
            external = None  # fixture absent -> skip, never fabricate

    # --- lead-time-vs-POD curve (hazards with a forecast horizon only)
    leadtime = None
    if spec.lead_hours and spec.horizon_labels_train and spec.horizon_labels_test:
        lt_rows = _cap(list(zip(spec.Xtr, spec.horizon_labels_train, strict=False)), fit_cap)
        leadtime = leadtime_to_dict(
            lead_time_curve(
                [r[0] for r in lt_rows],
                [r[1] for r in lt_rows],
                spec.Xte,
                spec.horizon_labels_test,
                spec.lead_hours,
                factory,
                target_pod=spec.target_pod,
            )
        )

    return {
        "source": spec.source,
        "label": spec.label_desc,
        "split": spec.split_desc,
        "features": list(spec.feature_names),
        "train_size": len(spec.ytr),
        "test_size": len(spec.yte),
        "train_base_rate": round(sum(spec.ytr) / max(1, len(spec.ytr)), 4),
        "test_base_rate": round(sum(spec.yte) / max(1, len(spec.yte)), 4),
        "model": _metrics(spec.yte, p_te),
        "model_raw": _metrics(spec.yte, p_te_raw),
        "model_weights": {n: round(w, 3) for n, w in zip(spec.feature_names, model.weights, strict=False)},
        "baseline_comparisons": comparisons,
        "decision": decision,
        "calibration": calibration,
        "conformal": conformal,
        "fairness": fairness,
        "tail": tail,
        "cv_leave_one_region_out": summarise(loro_folds),
        "cv_rolling_origin": summarise(rolling_folds),
        "drift": [d.to_dict() for d in drifts],
        "retrain_decision": retrain.to_dict(),
        "leadtime": leadtime,
        "robustness": robustness,
        "external_outcome": external,
        "notes": spec.notes,
    }


# ----------------------------------------------------------------- hazard specs
def quake_spec(path: str | None = None) -> HazardSpec:
    """Earthquake spec — DAMAGING-outcome track on the real USGS catalog.

    Label: measured damage-grade outcome (ShakeMap MMI >= VI or PAGER >= yellow).
    Baseline: the fixed GMPE-style attenuation score (what an agency computes
    with no ML). PAGER itself is reserved for the felt track — it is part of
    this label, so using it here would be circular.
    """
    quakes = load_quakes(path) if path else load_quakes()
    train, test = temporal_split(quakes)

    def magband(q) -> str:
        if q.mag >= 6.5:
            return "mag:6.5+"
        return "mag:5.5-6.5" if q.mag >= 5.5 else "mag:4.5-5.5"

    return HazardSpec(
        name="earthquake-damaging",
        source="USGS FDSN historical catalog 2013-2017 (real earthquakes, M4.5+)",
        label_desc="damage-grade outcome: ShakeMap MMI>=VI or PAGER alert>=yellow",
        split_desc="temporal: train 2013-2015, test 2016-2017 (out-of-sample)",
        feature_names=FEATURE_NAMES,
        Xtr=[q.features() for q in train],
        ytr=[q.label_damaging() for q in train],
        Xte=[q.features() for q in test],
        yte=[q.label_damaging() for q in test],
        baselines_test={"gmpe_attenuation": [q.gmpe_score() for q in test]},
        fairness_axes_test={
            "region": [q.region() for q in test],
            "magnitude_band": [magband(q) for q in test],
            "depth_band": [
                "depth:shallow<70km" if q.depth_km < 70 else "depth:deep>=70km" for q in test
            ],
        },
        fairness_axes_train={
            "region": [q.region() for q in train],
            "magnitude_band": [magband(q) for q in train],
            "depth_band": [
                "depth:shallow<70km" if q.depth_km < 70 else "depth:deep>=70km" for q in train
            ],
        },
        severity_test=[{"mag": q.mag, "mmi": q.mmi} for q in test],
        slices=[
            SeveritySlice("M6.0+", lambda s: s["mag"] >= 6.0),
            SeveritySlice("M6.5+", lambda s: s["mag"] >= 6.5),
            SeveritySlice("M7.0+", lambda s: s["mag"] >= 7.0),
        ],
        X_all=[q.features() for q in quakes],
        y_all=[q.label_damaging() for q in quakes],
        regions_all=[q.region() for q in quakes],
        years_all=[q.year for q in quakes],
        notes=[
            "PAGER is excluded as a baseline on this label because the label "
            "partially derives from PAGER; see felt_vs_pager for that comparison.",
        ],
    )


def quake_felt_vs_pager(
    path: str | None = None, *, n_boot: int = 250, seed: int = 0
) -> dict[str, Any]:
    """Model vs the OPERATIONAL incumbent (USGS PAGER) on the PAGER-free felt label.

    PAGER targets fatalities/losses, so on 'was it felt' it is a conservative
    comparator — but it is the alert agencies actually receive today, which
    makes 'do we add information over it?' the question that matters. The GMPE
    attenuation baseline is reported on the same label for scale.
    """
    quakes = load_quakes(path) if path else load_quakes()
    train, test = temporal_split(quakes)
    Xtr, ytr = [q.features() for q in train], [q.label_felt() for q in train]
    Xte, yte = [q.features() for q in test], [q.label_felt() for q in test]
    rows = _cap(list(zip(Xtr, ytr, strict=False)), 12000)
    model = fit_logistic(
        [r[0] for r in rows], [r[1] for r in rows], name="felt", epochs=150, balanced=True
    )
    p_te = predict(model, Xte)
    p_pager = [q.pager_alarm() for q in test]
    p_gmpe = [q.gmpe_score() for q in test]
    return {
        "label": "felt reports > 0 (PAGER-free, so PAGER can be compared fairly)",
        "model": _metrics(yte, p_te),
        "vs_pager": {
            "auc": compare_auc(yte, p_te, p_pager, n_boot=n_boot, seed=seed).to_dict(),
            "brier": compare_brier(yte, p_te, p_pager, n_boot=n_boot, seed=seed).to_dict(),
        },
        "vs_gmpe": {
            "auc": compare_auc(yte, p_te, p_gmpe, n_boot=n_boot, seed=seed).to_dict(),
        },
    }


def flood_spec(path: str | None = None) -> HazardSpec:
    """Flood spec — real GloFAS discharge outcomes over Indian basins."""
    rows = flood_ds.load_rows(path) if path else flood_ds.load_rows()
    train, test = flood_ds.temporal_split(rows)
    Xtr, ytr = flood_ds.to_xy(train)
    Xte, yte = flood_ds.to_xy(test)
    return HazardSpec(
        name="flood",
        source="GloFAS-ERA5 river-discharge reanalysis + ERA5 precipitation, "
        "12 Indian river-basin sites, 2010-2023 daily (real outcomes)",
        label_desc="discharge reaches the site's train-climatology q95 flood "
        "threshold within the next 1-3 days",
        split_desc="temporal: train 2010-2018, test 2019-2023 (five held-out monsoons)",
        feature_names=flood_ds.FEATURE_NAMES,
        Xtr=Xtr,
        ytr=ytr,
        Xte=Xte,
        yte=yte,
        baselines_test={
            "persistence": [r.persistence for r in test],
            "seasonal_climatology": [r.climatology for r in test],
        },
        fairness_axes_test={
            "setting": [f"setting:{r.setting}" for r in test],
            "region": [f"region:{r.region}" for r in test],
            "basin": [f"basin:{r.basin}" for r in test],
        },
        fairness_axes_train={
            "setting": [f"setting:{r.setting}" for r in train],
            "region": [f"region:{r.region}" for r in train],
            "basin": [f"basin:{r.basin}" for r in train],
        },
        severity_test=[{"ratio": r.severity, "severe": r.severe} for r in test],
        slices=[
            SeveritySlice("peak >=1.2x flood threshold", lambda s: s["ratio"] >= 1.2),
            SeveritySlice("severe (train q99)", lambda s: bool(s["severe"])),
        ],
        X_all=[list(r.features) for r in rows],
        y_all=[r.label for r in rows],
        regions_all=[r.region for r in rows],
        years_all=[r.year for r in rows],
        horizon_labels_train=[r.horizon_labels for r in train],
        horizon_labels_test=[r.horizon_labels for r in test],
        lead_hours=tuple(h * 24 for h in flood_ds.HORIZONS),
        dates_test=[r.date for r in test],
        notes=[
            "Flood threshold (q95) and severe threshold (q99) derive from TRAIN "
            "years only — the test period cannot define its own events.",
            "Persistence (today's discharge vs threshold) is the standard "
            "operational no-model forecast; beating it is the real bar.",
        ],
    )


def fire_spec(path: str | None = None) -> HazardSpec:
    """Fire spec — real FPA-FOD wildfire occurrences in the Pacific Northwest."""
    rows = fire_ds.load_rows(path) if path else fire_ds.load_rows()
    train, test = fire_ds.temporal_split(rows)
    Xtr, ytr = fire_ds.to_xy(train)
    Xte, yte = fire_ds.to_xy(test)
    return HazardSpec(
        name="fire",
        source="USDA FPA-FOD wildfire occurrences + ERA5 fire weather, 12 "
        "Pacific-Northwest cells, 2012-2018 daily (real outcomes)",
        label_desc=">=1 agency-reported wildfire discovered in the cell on day t+1",
        split_desc="temporal: train 2012-2016, test 2017-2018 (incl. the record "
        "2017 season)",
        feature_names=fire_ds.FEATURE_NAMES,
        Xtr=Xtr,
        ytr=ytr,
        Xte=Xte,
        yte=yte,
        baselines_test={"angstrom_index": [r.angstrom_score for r in test]},
        fairness_axes_test={
            "region": [f"region:{r.region}" for r in test],
            "state": [f"state:{r.state}" for r in test],
        },
        fairness_axes_train={
            "region": [f"region:{r.region}" for r in train],
            "state": [f"state:{r.state}" for r in train],
        },
        severity_test=[{"acres": r.severity} for r in test],
        slices=[
            SeveritySlice("fire >=100 acres next day", lambda s: s["acres"] >= 100.0),
            SeveritySlice("fire >=1000 acres next day", lambda s: s["acres"] >= 1000.0),
        ],
        X_all=[list(r.features) for r in rows],
        y_all=[r.label for r in rows],
        regions_all=[r.region for r in rows],
        years_all=[r.year for r in rows],
        horizon_labels_train=[r.horizon_labels for r in train],
        horizon_labels_test=[r.horizon_labels for r in test],
        lead_hours=tuple(h * 24 for h in fire_ds.HORIZONS),
        notes=[
            "Study region is OR+WA because that is the public FPA-FOD layer's "
            "verified 2012-2018 coverage; provenance is recorded in the fixture.",
            "The Angström index baseline is the operational fire-weather formula, "
            "computed from the same day's weather as the model's features.",
        ],
    )


def india_fire_spec(path: str | None = None) -> HazardSpec:
    """Fire spec — REAL India fire validation (NASA FIRMS VIIRS detections + ERA5).

    Replaces the Pacific-NW FPA-FOD geography with genuine Indian fire-belt cells
    and India's Feb-May fire season. Same leak-free methodology; severity is FRP
    (fire radiative power) instead of acres.
    """
    rows = fire_ds.load_rows(path) if path else fire_ds.load_rows_india()
    train, test = fire_ds.temporal_split_by_date(rows, fire_ds.INDIA_SPLIT_DATE)
    Xtr, ytr = fire_ds.to_xy(train)
    Xte, yte = fire_ds.to_xy(test)
    return HazardSpec(
        name="fire-india",
        source="NASA FIRMS (VIIRS-SNPP) active-fire detections + ERA5 fire weather, "
        "10 Indian fire-belt cells, 2015-2024 daily (~239k in-cell detections, real)",
        label_desc=">=1 FIRMS active-fire detection in the cell on day t+1",
        split_desc="temporal: train 2015-2021 fire seasons, test 2022-2024 "
        "(three held-out seasons, out-of-sample; no cross-year leakage)",
        feature_names=fire_ds.FEATURE_NAMES,
        Xtr=Xtr,
        ytr=ytr,
        Xte=Xte,
        yte=yte,
        baselines_test={"angstrom_index": [r.angstrom_score for r in test]},
        fairness_axes_test={
            "region": [f"region:{r.region}" for r in test],
            "state": [f"state:{r.state}" for r in test],
        },
        fairness_axes_train={
            "region": [f"region:{r.region}" for r in train],
            "state": [f"state:{r.state}" for r in train],
        },
        severity_test=[{"frp": r.severity} for r in test],
        slices=[
            SeveritySlice("FRP >=50 next day", lambda s: s["frp"] >= 50.0),
            SeveritySlice("FRP >=100 next day", lambda s: s["frp"] >= 100.0),
        ],
        X_all=[list(r.features) for r in rows],
        y_all=[r.label for r in rows],
        regions_all=[r.region for r in rows],
        years_all=[r.year for r in rows],
        horizon_labels_train=[r.horizon_labels for r in train],
        horizon_labels_test=[r.horizon_labels for r in test],
        lead_hours=tuple(h * 24 for h in fire_ds.HORIZONS),
        notes=[
            "Real Indian geography + Feb-May fire season (replaces the Pacific-NW "
            "FPA-FOD validation — FIRMS was unreachable when that was first built).",
            "Label is a FIRMS satellite active-fire detection; severity is FRP "
            "(fire radiative power), the satellite intensity proxy, not acres.",
            "The Angström index baseline is the operational fire-weather formula.",
        ],
    )


HAZARDS: dict[str, Callable[[], HazardSpec]] = {
    "earthquake": quake_spec,
    "flood": flood_spec,
    "fire": fire_spec,
    "fire-india": india_fire_spec,
}


def run_validation(
    hazards: Sequence[str] | None = None,
    *,
    fast: bool = False,
    seed: int = 0,
) -> dict[str, Any]:
    """Run the full battery for the requested hazards (default: all three).

    ``fast`` shrinks epochs and bootstrap rounds for a quicker (still real-data)
    pass. Returns one JSON-serialisable dict ``{"methodology", "hazards": {...}}``;
    the earthquake section additionally carries the felt-vs-PAGER incumbent
    comparison.
    """
    if hazards:
        chosen = list(hazards)
    else:
        # Default = core three + India fire only if its fixture is present (the
        # FIRMS pull is network-dependent; a missing fixture must not break the run).
        import os as _os

        chosen = ["earthquake", "flood", "fire"]
        if _os.path.exists(fire_ds.INDIA_FIXTURE):
            chosen.append("fire-india")
    knobs: dict[str, Any] = (
        {
            "epochs": 80,
            "cv_epochs": 40,
            "fit_cap": 6000,
            "cv_cap": 3000,
            "n_boot": 100,
            "tail_boot": 80,
        }
        if fast
        else {}
    )
    report: dict[str, Any] = {
        "methodology": "real data only; leak-free features; temporal + blocked-"
        "spatial validation; thresholds and calibrators fitted on calibration "
        "splits, never on test",
        "hazards": {},
    }
    for name in chosen:
        if name not in HAZARDS:
            raise ValueError(f"unknown hazard {name!r}; choose from {sorted(HAZARDS)}")
        report["hazards"][name] = evaluate_hazard(HAZARDS[name](), seed=seed, **knobs)
        if name == "earthquake":
            report["hazards"][name]["felt_vs_pager"] = quake_felt_vs_pager(
                n_boot=(100 if fast else 250), seed=seed
            )
    return report


# ------------------------------------------------------------------ markdown view
def _md_comparison(name: str, comp: dict[str, Any]) -> str:
    a = comp["auc"]
    sig = "YES" if a["significant_at_5pct"] else "no"
    return (
        f"| {name} | {a['baseline']:.4f} | {a['model']:.4f} | "
        f"{a['delta_mean']:+.4f} [{a['delta_ci95'][0]:+.4f}, {a['delta_ci95'][1]:+.4f}] | "
        f"{a['p_value']:.4f} | {sig} |"
    )


def to_markdown(report: dict[str, Any]) -> str:
    """Render the multi-hazard validation report as Markdown."""
    lines = [
        "# DisasterMind — Real-Data Validation (all hazards)",
        "",
        f"_{report['methodology']}_",
    ]
    for hname, h in report.get("hazards", {}).items():
        m = h["model"]
        d = h["decision"]
        pod_pt = d["test_at_target_pod"]
        loro = h["cv_leave_one_region_out"]
        roll = h["cv_rolling_origin"]
        lines += [
            "",
            f"## {hname.title()}",
            "",
            f"- **Source:** {h['source']}",
            f"- **Label:** {h['label']}",
            f"- **Split:** {h['split']}",
            f"- **Train:** {h['train_size']} rows (base rate {h['train_base_rate']:.2%}) · "
            f"**Test:** {h['test_size']} rows (base rate {h['test_base_rate']:.2%})",
            "",
            f"**Headline (calibrated, out-of-sample):** AUC {m['auc']} · Brier {m['brier']} · "
            f"ECE {m['ece']}",
            "",
            "### vs operational baselines (paired bootstrap on AUC)",
            "| Baseline | Baseline AUC | Model AUC | dAUC [95% CI] | p | significant |",
            "|---|---|---|---|---|---|",
        ]
        for bname, comp in h["baseline_comparisons"].items():
            lines.append(_md_comparison(bname, comp))
        lines += [
            "",
            "### At the operating point (threshold chosen on calibration split)",
            f"- Target POD {d['target_pod']:.0%} -> threshold "
            f"{d['threshold_from_calibration']:.3f}",
            f"- On test: POD {pod_pt['pod']:.2%}, FAR {pod_pt['far']:.2%}, "
            f"CSI {pod_pt['csi']:.3f}, bias {pod_pt['bias']:.2f} "
            f"(tp={pod_pt['tp']}, fp={pod_pt['fp']}, fn={pod_pt['fn']})",
            f"- Cost model (miss:false-alarm = {d['cost_assumptions']['miss']:.0f}:"
            f"{d['cost_assumptions']['false_alarm']:.0f}): test cost "
            f"{d['test_cost_total']:.0f} at the cost-optimal threshold",
            "",
            "### Calibration + conformal coverage",
            f"- ECE raw {h['calibration']['ece_raw']:.4f} -> calibrated "
            f"{h['calibration']['ece_calibrated']:.4f} (isotonic, fit on calibration split)",
            f"- Conformal: target coverage {h['conformal']['target_coverage']:.0%}, "
            f"empirical {h['conformal']['coverage']:.2%}, singleton rate "
            f"{h['conformal']['singleton_rate']:.2%}",
            "",
            "### Blocked generalisation",
            f"- Leave-one-region-out: worst AUC {loro['auc_worst']}, mean "
            f"{loro['auc_mean']} over {loro['folds']} regions",
            f"- Rolling origin: worst AUC {roll['auc_worst']}, mean "
            f"{roll['auc_mean']} over {roll['folds']} years",
            "",
            "### Fairness audit (shared threshold)",
        ]
        for axis, audit in h["fairness"].items():
            flag = ", ".join(audit["under_protected_groups"]) or "none"
            lines.append(
                f"- **{axis}**: {'PASS' if audit['passed'] else 'FLAGGED'} "
                f"(under-protected: {flag})"
            )
            rem = audit.get("remediation")
            if rem:
                after = rem["after"]
                cost = ", ".join(
                    f"{g} +{c:.0%} FAR" for g, c in rem["far_cost_of_equity"].items()
                )
                lines.append(
                    f"  - remediation (per-group thresholds, fit on calibration): "
                    f"{'PASS' if after['passed'] else 'still flagged: ' + ', '.join(after['under_protected_groups'])}"
                    + (f" — cost of equity: {cost}" if cost else "")
                )
                for g, cause in rem.get("residual_cause", {}).items():
                    lines.append(f"    - _{g}: {cause}_")
        if h.get("tail"):
            lines += ["", "### Rare-severe tail"]
            for s in h["tail"]["slices"]:
                if s["pod"] is not None:
                    lines.append(
                        f"- {s['slice']}: {s['events']} events, POD {s['pod']:.2%} "
                        f"[{s['pod_ci95'][0]:.2%}, {s['pod_ci95'][1]:.2%}]"
                    )
                else:
                    lines.append(
                        f"- {s['slice']}: no events in the test window "
                        "(reported, not hidden)"
                    )
        if h.get("leadtime"):
            lt = h["leadtime"]
            lines += [
                "",
                "### Lead time vs POD (actionable warning)",
                f"- Actionable lead time at POD 80%: "
                f"**{lt['actionable_lead_hours_at_pod80']} h**",
                "| Lead (h) | POD | FAR | AUC | events |",
                "|---|---|---|---|---|",
            ]
            for p in lt["curve"]:
                lines.append(
                    f"| {p['lead_hours']} | {p['pod']:.2%} | {p['far']:.2%} | "
                    f"{p['auc']} | {p['events']} |"
                )
        if h.get("robustness"):
            rb = h["robustness"]
            lines += [
                "",
                "### Degraded-input robustness (fixed model, sensors failing)",
                f"- Graceful until POD 70%: **{rb['graceful_until_pod70']:.0%}** of inputs down",
                "| Inputs lost | POD | FAR | AUC |",
                "|---|---|---|---|",
            ]
            for p in rb["curve"]:
                lines.append(
                    f"| {p['fraction']:.0%} | {p['pod']:.2%} | {p['far']:.2%} | {p['auc']} |"
                )
        ext = h.get("external_outcome")
        if ext and ext.get("auc_vs_declared_events") is not None:
            g = ext["mean_risk_by_class"]
            lines += [
                "",
                "### External cross-check vs GDACS declared floods (survey-grade)",
                f"- AUC separating declared-flood days from quiet days: "
                f"**{ext['auc_vs_declared_events']}** "
                f"({ext['n_declared_event_rows']}/{ext['n_rows']} declared, label external "
                "to the model)",
                f"- Severity gradient (mean risk): Red {g.get('red')} · "
                f"Orange {g.get('orange')} · quiet {g.get('quiet')}",
                "- _Country-level GDACS vs basin-specific sites -> temporal national "
                "check, not per-basin localisation._",
            ]
        rd = h["retrain_decision"]
        lines += [
            "",
            "### Drift + retraining",
            f"- Retrain now: **{rd['retrain']}**"
            + (f" — {'; '.join(rd['reasons'])}" if rd["reasons"] else " (no drift signal fired)"),
        ]
        for note in h.get("notes", []):
            lines.append(f"- _Note: {note}_")
        if "felt_vs_pager" in h:
            fp = h["felt_vs_pager"]
            a = fp["vs_pager"]["auc"]
            lines += [
                "",
                "### Incumbent check: felt label vs USGS PAGER",
                f"- Model AUC {a['model']:.4f} vs PAGER {a['baseline']:.4f} "
                f"(delta {a['delta_mean']:+.4f}, p={a['p_value']:.4f})",
            ]
    return "\n".join(lines)
