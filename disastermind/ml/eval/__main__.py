"""CLI entry point: ``python -m disastermind.ml.eval [--out <dir>]``.

Backtests a risk model for every module (A/B/C) on deterministic synthetic data
held-out splits, then prints the result as JSON. With ``--out`` it also writes
``backtest.json`` and a Markdown model card per module. Stdlib-only (argparse +
json); no network. A given ``--seed`` reproduces the same result exactly.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .backtest import DEFAULT_N, DEFAULT_TEST_FRACTION, backtest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m disastermind.ml.eval",
        description="Backtest + score per-module risk models on synthetic data.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="optional output directory for backtest.json + model cards",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="seed for the deterministic synthetic data + split (default: 0)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=DEFAULT_N,
        help=f"synthetic rows per module before split (default: {DEFAULT_N})",
    )
    parser.add_argument(
        "--test-fraction",
        type=float,
        default=DEFAULT_TEST_FRACTION,
        help=f"held-out test fraction (default: {DEFAULT_TEST_FRACTION})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = backtest(
        args.out, n=args.n, test_fraction=args.test_fraction, seed=args.seed
    )
    json.dump(result, sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
