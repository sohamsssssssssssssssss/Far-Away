"""``run_diagnostics`` — the DisasterMind self-check ("doctor").

Runs every probe in :mod:`disastermind.diagnostics.checks` and folds the results
into a single :class:`~disastermind.diagnostics.report.Report`. Stdlib-only and
offline; optional backends are probed lazily and only ever downgrade to WARN/SKIP
so a clean install on a laptop with no Postgres/Kafka still reports a *bootable*
system (PRD Step 10 graceful degradation).

Usage::

    from disastermind.diagnostics import run_diagnostics
    report = run_diagnostics()
    print(report.to_markdown())
    raise SystemExit(report.exit_code)
"""
from __future__ import annotations

from .checks import (
    check_audit,
    check_backends,
    check_config,
    check_dag,
    check_modules,
)
from .report import Report, Status


def run_diagnostics(settings=None, audit_path: str | None = None) -> Report:
    """Run the full doctor and return a :class:`Report`.

    Parameters
    ----------
    settings:
        A :class:`~disastermind.core.config.Settings` (or compatible). Defaults
        to a freshly-constructed ``Settings()`` reading the environment.
    audit_path:
        Path to a JSONL audit log whose hash-chain should be verified. When
        ``None`` we fall back to ``settings.audit_log_path`` *only if that file
        exists*; otherwise the audit probe is skipped.
    """
    if settings is None:
        from ..core.config import Settings

        settings = Settings()

    report = Report()
    report.meta["package"] = "disastermind.diagnostics"

    # (a) modules import + build. Returns a wired env we hand to later probes.
    env = check_modules(report, settings)
    if not env:
        env = {"settings": settings}

    # (b) topic DAG balance (dry build + one seeded cycle).
    try:
        check_dag(report, env)
    except Exception as exc:  # pragma: no cover - probe must not abort the doctor
        report.add("dag", Status.FAIL, f"DAG probe crashed: {exc!r}")

    # (c) config sanity.
    check_config(report, settings)

    # (d) audit-chain verification.
    resolved_audit = _resolve_audit_path(audit_path, settings)
    check_audit(report, resolved_audit)

    # (e) OPTIONAL backend reachability (lazy; never FAILs).
    check_backends(report, settings)

    return report


def _resolve_audit_path(audit_path: str | None, settings) -> str | None:
    """Pick the audit path to verify: explicit arg wins; else settings if it exists."""
    if audit_path:
        return audit_path
    import os

    candidate = getattr(settings, "audit_log_path", "") or ""
    if candidate and os.path.exists(candidate):
        return candidate
    return None
