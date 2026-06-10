"""CLI entry point: ``python -m disastermind.demo [A|B|C] [--escalate]``.

Prints the narrated transcript as Markdown (or JSON with ``--json``). Offline and
deterministic — no network, no wall-clock dependence.
"""
from __future__ import annotations

import argparse
import json
import sys

from .runner import DEMO_MODULES, run_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m disastermind.demo",
        description="Run the narrated DisasterMind golden-path demo, offline.",
    )
    parser.add_argument(
        "module",
        nargs="?",
        default="B",
        help="disaster module: A=cyclone/flood, B=earthquake, C=urban fire/collapse",
    )
    parser.add_argument(
        "--escalate",
        action="store_true",
        help="drive the human-approval (escalation) path through the scenario",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the transcript as JSON instead of Markdown",
    )
    args = parser.parse_args(argv)

    key = (args.module or "B").strip().upper()
    if key not in DEMO_MODULES:
        parser.error(f"module must be one of {', '.join(DEMO_MODULES)} (got {args.module!r})")

    transcript = run_demo(module=key, escalate=args.escalate)
    if args.json:
        print(json.dumps(transcript.to_dict(), indent=2, sort_keys=True))
    else:
        print(transcript.to_markdown())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
