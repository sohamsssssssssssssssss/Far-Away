"""DisasterMind system self-check ("doctor").

Run the whole diagnosis and inspect / render the result::

    from disastermind.diagnostics import run_diagnostics
    report = run_diagnostics(audit_path="./audit.jsonl")
    print(report.to_markdown())   # human-readable
    print(report.to_dict())       # machine-readable
    raise SystemExit(report.exit_code)

Or from the shell::

    python -m disastermind.diagnostics          # markdown + exit code
    python -m disastermind.diagnostics --json    # JSON

Everything here is stdlib-only and offline. Optional backends (Postgres, Kafka,
Elasticsearch, ...) are probed lazily via ``disastermind.integrations.health``
*iff* that module is importable, and an unreachable backend only ever produces a
WARN/SKIP — never a hard failure (PRD Step 10 graceful degradation).
"""
from __future__ import annotations

from .checks import (
    analyse_dag,
    check_audit,
    check_backends,
    check_config,
    check_dag,
    check_modules,
    known_contract_topics,
    produced_topics,
    subscribed_topics,
)
from .doctor import run_diagnostics
from .report import Check, Report, Status

__all__ = [
    "run_diagnostics",
    "Report",
    "Check",
    "Status",
    "analyse_dag",
    "check_modules",
    "check_dag",
    "check_config",
    "check_audit",
    "check_backends",
    "subscribed_topics",
    "produced_topics",
    "known_contract_topics",
]
