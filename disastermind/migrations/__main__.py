"""``python -m disastermind.migrations [--dry-run]`` — apply or preview migrations.

Exit code is ``0`` on success (including a clean dry-run) and ``1`` only when a
live apply reported an error, so it can gate a deploy step.
"""
from __future__ import annotations

import argparse
import sys

from ..core.config import Settings
from .migrations import apply_migrations, format_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m disastermind.migrations",
        description=(
            "Create and version the DisasterMind durable schema (PostGIS + "
            "TimescaleDB). Offline-safe: without a configured durable backend "
            "this is a no-connection dry-run."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Never connect; just list the migrations that would run.",
    )
    args = parser.parse_args(argv)

    report = apply_migrations(Settings(), dry_run=args.dry_run)
    print(format_report(report))
    return 1 if report.get("error") else 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
