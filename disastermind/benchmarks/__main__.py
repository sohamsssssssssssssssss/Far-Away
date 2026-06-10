"""``python -m disastermind.benchmarks`` — run a deterministic load demo.

Drives a small batch of synthetic A/B/C incidents through the full agent DAG for
a few cycles and prints the resulting throughput report as Markdown. Everything
is count-based and offline (PRD HARD RULE 2), so the output is stable across
machines and safe to snapshot.

Optional CLI flags (all have deterministic defaults)::

    python -m disastermind.benchmarks [-n N] [-c CYCLES] [-m A,B,C]
                                      [--history-cap CAP] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import TextIO

from .harness import drive_n_incidents
from .report import report, to_markdown


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m disastermind.benchmarks",
        description="Deterministic DisasterMind throughput benchmark (no wall clock).",
    )
    p.add_argument(
        "-n", "--incidents", type=int, default=12,
        help="number of synthetic incidents to inject (default: 12)",
    )
    p.add_argument(
        "-c", "--cycles", type=int, default=3,
        help="coordination cycles to drive (default: 3)",
    )
    p.add_argument(
        "-m", "--modules", type=str, default="A,B,C",
        help="comma-separated modules to round-robin (default: A,B,C)",
    )
    p.add_argument(
        "--history-cap", type=int, default=2000,
        help="bus history ring-buffer cap (default: 2000)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="emit the normalised report as JSON instead of Markdown",
    )
    return p


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the demo benchmark. Returns 0. ``out`` is injectable for tests."""
    out = out or sys.stdout
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    modules = [m.strip().upper() for m in args.modules.split(",") if m.strip()]
    result = drive_n_incidents(
        args.incidents,
        cycles=args.cycles,
        modules=modules,
        history_cap=args.history_cap,
    )
    if args.json:
        out.write(json.dumps(report(result), indent=2, sort_keys=True))
        out.write("\n")
    else:
        out.write(to_markdown(result))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
