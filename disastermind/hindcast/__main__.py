"""``python -m disastermind.hindcast`` — replay Cyclone Fani (2019).

Prints the honest event report (activation lead time, landfall error at
decreasing lead, plan produced) against the documented outcome. ``--json`` emits
the raw results.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .fani import load_amphan, load_fani
from .replay import run_hindcast
from .report import to_markdown

LEADS = (72.0, 48.0, 36.0, 24.0, 12.0)
LOADERS = {"fani": load_fani, "amphan": load_amphan}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="disastermind.hindcast")
    ap.add_argument("--storm", choices=sorted(LOADERS), default="fani",
                    help="which real cyclone to replay (default: fani)")
    ap.add_argument("--backtest", action="store_true",
                    help="run the FULL-PIPELINE backtest across all real cyclones "
                         "(forecast -> evacuation decision -> scored vs reality)")
    ap.add_argument("--lead", type=int, default=72,
                    help="forecast cutoff in hours before landfall (backtest; default 72)")
    ap.add_argument("--json", action="store_true", help="emit raw results as JSON")
    args = ap.parse_args(argv)

    if args.backtest:
        from .pipeline_backtest import run_backtest
        from .pipeline_backtest import to_markdown as backtest_md

        report = run_backtest(lead=args.lead)
        print(json.dumps(report, indent=2) if args.json else backtest_md(report))
        return 0

    case = LOADERS[args.storm]()
    results = [run_hindcast(case, lead_hours=h) for h in LEADS]
    if args.json:
        print(json.dumps({"outcome": case.outcome, "results": [asdict(r) for r in results]}, indent=2))
    else:
        print(to_markdown(case, results))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
