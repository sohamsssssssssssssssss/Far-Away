"""``python -m disastermind.live`` — the production live-runtime entry point.

Builds a :class:`~disastermind.live.system.LiveSystem` from the environment
(:class:`~disastermind.core.config.Settings`) and drives the coordination loop.
Offline-safe by default: with no ``DM_*`` overrides it runs the deterministic
in-memory system, so this command starts and exits cleanly on a laptop with no
Kafka / Postgres / network (PRD Step 10 graceful degradation).

Flags (all optional; stdlib ``argparse`` only)::

    --max-cycles N   stop after N coordination cycles (default: 1, so the
                     module is safe to invoke in CI/smoke-tests)
    --live           attach real backends where DSNs/libs are present (each
                     still degrades to its in-memory fallback if unreachable)
    --health         print the health() dict as JSON and exit (no loop)

The command prints a one-line build summary, then either the health report or a
final cycle count, and returns a conventional process exit code.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .system import LiveSystem


def main(argv: Sequence[str] | None = None, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="disastermind.live",
        description="DisasterMind live runtime (offline-safe by default).",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=1,
        help="number of coordination cycles to run (default: 1).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="attach real backends where configured (each degrades to fallback).",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="print the health() report as JSON and exit without running the loop.",
    )
    args = parser.parse_args(argv)

    system = LiveSystem.build(live=args.live)
    print(
        "disastermind.live: built "
        f"(live={system.live} bus={system.meta.get('bus')} "
        f"degraded_modules={len(system.meta.get('degraded_modules', []))})",
        file=out,
    )

    if args.health:
        print(json.dumps(system.health(), indent=2, default=str), file=out)
        return 0

    cycles = system.run(max_cycles=args.max_cycles)
    print(f"disastermind.live: ran {cycles} cycle(s)", file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
