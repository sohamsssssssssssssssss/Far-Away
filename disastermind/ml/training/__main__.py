"""CLI entry point: ``python -m disastermind.ml.training --out <dir>``.

Trains + saves a risk model for every module (A/B/C) on deterministic synthetic
data, then prints the resulting manifest as JSON. Stdlib-only (argparse + json);
no network. A given ``--seed`` reproduces the same artefacts exactly.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .train import DEFAULT_N, train_all


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m disastermind.ml.training",
        description="Train + persist per-module risk models on synthetic data.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="output directory for the model artefacts + manifest.json",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="seed for the deterministic synthetic data (default: 0)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=DEFAULT_N,
        help=f"synthetic training rows per module (default: {DEFAULT_N})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = train_all(args.out, n=args.n, seed=args.seed)
    json.dump(manifest, sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
