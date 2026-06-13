"""Calibrate the evacuation clearance model against real historical records.

The clearance model (:func:`disastermind.evacuation.clearance.estimate_clearance`)
is honest that its parameters — ``mobilization_hours``, ``participation``,
per-class egress rates — are **explicit, unvalidated planning assumptions**. The
single most credibility-moving step for the evacuation layer is to replace those
assumptions with values *fit to real district evacuation records*.

This module is that harness. Given a set of observed evacuations
(:class:`EvacRecord` — zone population, egress capacity, and the **actual**
clearance time the agency recorded), it:

  * scores the current default parameters against reality (mean absolute error in
    hours, bias, per-record residuals);
  * fits ``mobilization_hours`` and an effective ``participation`` by least-squares
    to the observed clearance times (the model is linear in these two terms, so the
    fit is closed-form and deterministic — no solver, no network);
  * reports error **before vs. after** calibration, so the improvement is explicit;
  * emits the calibrated parameters as a plain dict to feed back into planning.

Stdlib only. The harness does not invent data — it needs real records (see
``docs/EVAC_CALIBRATION.md`` for the collection protocol and CSV schema). Until
those exist, the planning parameters remain labelled UNVALIDATED everywhere they
surface, which is the honest state.
"""
from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass

from .clearance import estimate_clearance


@dataclass(frozen=True)
class EvacRecord:
    """One real, documented zone evacuation used as calibration ground truth."""

    zone: str
    population: int
    egress_capacity_pph: float
    observed_clearance_hours: float
    #: optional measured participation, if the agency recorded it
    observed_participation: float | None = None


@dataclass(frozen=True)
class CalibrationResult:
    n: int
    mae_before: float
    mae_after: float
    bias_before: float
    fitted_mobilization_hours: float
    fitted_participation: float
    per_record: list[dict]

    def improvement_pct(self) -> float:
        if self.mae_before <= 0:
            return 0.0
        return round(100.0 * (self.mae_before - self.mae_after) / self.mae_before, 1)


def _predicted(rec: EvacRecord, *, mobilization_hours: float, participation: float,
               last_mile_hours: float = 0.75) -> float:
    return estimate_clearance(
        rec.population,
        rec.egress_capacity_pph,
        participation=participation,
        mobilization_hours=mobilization_hours,
        last_mile_hours=last_mile_hours,
    ).clearance_hours


def _mae(records: Sequence[EvacRecord], *, mobilization_hours: float,
         participation: float) -> tuple[float, float]:
    """Return (mean-absolute-error, mean-signed-bias) in hours."""
    errs = [
        _predicted(r, mobilization_hours=mobilization_hours, participation=participation)
        - r.observed_clearance_hours
        for r in records
    ]
    n = len(errs) or 1
    mae = sum(abs(e) for e in errs) / n
    bias = sum(errs) / n
    return round(mae, 3), round(bias, 3)


def calibrate(
    records: Sequence[EvacRecord],
    *,
    default_mobilization_hours: float = 4.0,
    default_participation: float = 0.9,
    last_mile_hours: float = 0.75,
) -> CalibrationResult:
    """Fit mobilization + participation to observed clearance times.

    Clearance is ``mobilization + (population * participation)/egress + last_mile``.
    Holding participation at the agency-measured (or default) value, the only free
    additive term vs. observed is mobilization, fit as the mean residual — and if
    no per-record participation is given, we also fit a single effective
    participation by least squares over the egress-scaled demand. Both are
    closed-form and deterministic.
    """
    if not records:
        raise ValueError("need at least one EvacRecord to calibrate")

    mae_before, bias_before = _mae(
        records, mobilization_hours=default_mobilization_hours,
        participation=default_participation,
    )

    # Least-squares fit of the linear model:
    #   observed - last_mile = mobilization + participation * (population / egress)
    # unknowns: a = mobilization, b = participation. Standard 2-var normal equations.
    xs = [r.population / r.egress_capacity_pph if r.egress_capacity_pph > 0 else 0.0
          for r in records]
    ys = [r.observed_clearance_hours - last_mile_hours for r in records]
    n = len(records)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        # All records share the same egress-scaled demand: can't separate the two
        # terms. Fall back to fitting mobilization only, at default participation.
        fitted_participation = default_participation
        fitted_mob = sum(ys) / n - default_participation * (sx / n)
    else:
        fitted_participation = (n * sxy - sx * sy) / denom
        fitted_mob = (sy - fitted_participation * sx) / n
        # Participation is a fraction; clamp to a sane [0.1, 1.0] and keep mob >= 0.
        fitted_participation = max(0.1, min(1.0, fitted_participation))
        fitted_mob = max(0.0, fitted_mob)

    mae_after, _ = _mae(
        records, mobilization_hours=fitted_mob, participation=fitted_participation,
    )

    per_record = []
    for r in records:
        pred = _predicted(r, mobilization_hours=fitted_mob, participation=fitted_participation)
        per_record.append({
            "zone": r.zone,
            "population": r.population,
            "observed_h": r.observed_clearance_hours,
            "predicted_h": pred,
            "residual_h": round(pred - r.observed_clearance_hours, 2),
        })

    return CalibrationResult(
        n=n,
        mae_before=mae_before,
        mae_after=mae_after,
        bias_before=bias_before,
        fitted_mobilization_hours=round(fitted_mob, 3),
        fitted_participation=round(fitted_participation, 3),
        per_record=per_record,
    )


def load_records_csv(path: str) -> list[EvacRecord]:
    """Load calibration records from a CSV (schema in docs/EVAC_CALIBRATION.md).

    Required columns: zone, population, egress_capacity_pph, observed_clearance_hours.
    Optional: observed_participation.
    """
    out: list[EvacRecord] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            op = row.get("observed_participation")
            out.append(EvacRecord(
                zone=row["zone"],
                population=int(float(row["population"])),
                egress_capacity_pph=float(row["egress_capacity_pph"]),
                observed_clearance_hours=float(row["observed_clearance_hours"]),
                observed_participation=float(op) if op else None,
            ))
    return out
