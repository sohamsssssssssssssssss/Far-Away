"""``python -m disastermind.ml.validation`` — validate ALL hazards on real data.

Prints the Markdown report (skill vs operational baselines with significance,
decision-point POD/FAR, calibration + conformal coverage, blocked CV, fairness,
tail and drift) for earthquake, flood and fire. ``--hazard`` restricts to one,
``--fast`` shrinks fit/bootstrap effort, ``--json`` emits the raw report dict.
"""
from __future__ import annotations

import argparse
import json
import sys

from .run import HAZARDS, run_validation, to_markdown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="disastermind.ml.validation")
    ap.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    ap.add_argument(
        "--hazard",
        action="append",
        choices=sorted(HAZARDS),
        help="restrict to one hazard (repeatable; default: all)",
    )
    ap.add_argument("--fast", action="store_true", help="quicker pass (smaller fits/bootstraps)")
    args = ap.parse_args(argv)
    report = run_validation(args.hazard, fast=args.fast)
    print(json.dumps(report, indent=2) if args.json else to_markdown(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
