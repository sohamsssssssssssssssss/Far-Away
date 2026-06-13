#!/usr/bin/env python3
"""Regenerate every headline validation number and diff it against the published claims.

This is the project's reproducibility gate. It runs the full validation suite
offline against the committed real-data fixtures (no network, no optional deps,
deterministic), then asserts that each hazard's out-of-sample AUC / Brier / ECE
regenerates within tolerance of the golden snapshot in ``docs/validation_golden.json``
— the same figures published in ``PROJECT_OVERVIEW.md`` section 5.

A stranger with a clean checkout runs ``make reproduce`` (or ``python tools/reproduce.py``)
and watches the paper's numbers rebuild from raw fixtures, line by line, with a
PASS/FAIL verdict and a non-zero exit on any drift. That is the difference between
"trust the table" and "verify the table".

Exit code 0 == every metric reproduced within tolerance; 1 == drift or error.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = ROOT / "docs" / "validation_golden.json"
METRICS = ("auc", "brier", "ece")


def _load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text())


def _run_validation() -> dict:
    """Run the offline validation suite and return its parsed JSON report."""
    proc = subprocess.run(
        [sys.executable, "-m", "disastermind.ml.validation", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"validation suite exited {proc.returncode}")
    return json.loads(proc.stdout)


def main() -> int:
    golden = _load_golden()
    tol = golden["tolerance"]
    report = _run_validation()
    produced = report.get("hazards", {})

    print("\nDisasterMind — validation reproducibility check")
    print("=" * 78)
    print("Re-ran the full suite offline against committed fixtures and compared")
    print(f"every headline metric to the golden snapshot ({GOLDEN_PATH.relative_to(ROOT)}).\n")
    header = f"{'hazard':<13}{'metric':<7}{'claimed':>10}{'reproduced':>12}{'Δ':>9}  verdict"
    print(header)
    print("-" * 78)

    failures = 0
    for hazard, claims in golden["hazards"].items():
        model = produced.get(hazard, {}).get("model", {})
        for metric in METRICS:
            claimed = claims[metric]
            got = model.get(metric)
            if got is None:
                print(f"{hazard:<13}{metric:<7}{claimed:>10}{'MISSING':>12}{'':>9}  FAIL")
                failures += 1
                continue
            delta = abs(got - claimed)
            ok = delta <= tol[metric]
            verdict = "PASS" if ok else "FAIL"
            mark = "" if ok else "  <-- drift"
            print(f"{hazard:<13}{metric:<7}{claimed:>10.4f}{got:>12.4f}{delta:>9.4f}  {verdict}{mark}")
            if not ok:
                failures += 1
        print("-" * 78)

    total = len(golden["hazards"]) * len(METRICS)
    if failures:
        print(f"\nFAIL — {failures}/{total} metrics drifted beyond tolerance.\n")
        return 1
    print(f"\nPASS — all {total} headline metrics reproduced from raw fixtures.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
