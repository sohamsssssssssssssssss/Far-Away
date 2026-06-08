"""``python -m disastermind.diagnostics`` — run the doctor from the shell.

Prints a Markdown report (or JSON with ``--json``) and exits 0 when nothing
FAILED, 1 otherwise — so it composes with CI / shell ``&&`` chains. We avoid
editing the main ``cli.py`` (frozen / owned elsewhere); this is the package's own
self-contained entry point.
"""
from __future__ import annotations

import argparse
import sys

from .doctor import run_diagnostics


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m disastermind.diagnostics",
        description="DisasterMind system self-check (doctor).",
    )
    p.add_argument(
        "--audit-path",
        default=None,
        help="JSONL audit log whose hash-chain to verify (default: settings.audit_log_path if present).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON instead of Markdown.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_diagnostics(audit_path=args.audit_path)
    if args.json:
        sys.stdout.write(report.to_json() + "\n")
    else:
        sys.stdout.write(report.to_markdown() + "\n")
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
