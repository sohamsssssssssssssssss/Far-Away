"""Audit-log retention & rotation (PRD Step 9).

The decision log is an append-only, hash-chained JSONL file (tamper-evident).
Retention therefore must NEVER delete records from the middle of a live chain —
that would break :meth:`DecisionLogger.verify_chain`. Instead we **rotate**: seal
the current file by moving it intact to a timestamped archive, leaving a fresh
empty file for a new chain. Old archives are pruned by age/count.

Offline, stdlib-only, opt-in. Nothing here runs automatically; an operator or a
scheduled job calls :func:`rotate_audit_log` / :func:`enforce_retention` (see the
RUNBOOK). The active chain is always left verifiable.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass


@dataclass
class RotationResult:
    rotated: bool
    archived_path: str | None
    size_bytes: int
    reason: str


def _archive_name(path: str, stamp: str) -> str:
    base = os.path.basename(path)
    d = os.path.dirname(path) or "."
    return os.path.join(d, f"{base}.{stamp}.archive")


def rotate_audit_log(
    path: str,
    *,
    max_bytes: int = 50 * 1024 * 1024,
    stamp: str | None = None,
    force: bool = False,
) -> RotationResult:
    """Seal ``path`` into a timestamped archive when it exceeds ``max_bytes``.

    The current file is *moved* (atomic rename) so the sealed chain stays intact
    and independently verifiable; a brand-new empty log takes its place and a new
    hash chain begins. ``stamp`` is supplied by the caller (no wall-clock here, so
    callers stay deterministic — pass an ISO/epoch string). Returns a
    :class:`RotationResult`; ``rotated=False`` when under threshold and not forced.
    """
    if not os.path.exists(path):
        return RotationResult(False, None, 0, "no log file")
    size = os.path.getsize(path)
    if not force and size < max_bytes:
        return RotationResult(False, None, size, f"under threshold ({size} < {max_bytes})")
    if stamp is None:
        # Deterministic fallback derived from size+mtime, never the wall clock.
        stamp = f"{int(os.path.getmtime(path))}-{size}"
    archive = _archive_name(path, stamp)
    os.rename(path, archive)  # atomic seal of the intact chain
    open(path, "a", encoding="utf-8").close()  # fresh empty chain
    return RotationResult(True, archive, size, "rotated")


def list_archives(path: str) -> list[str]:
    """All sealed archives for ``path``, oldest first (by mtime)."""
    base = os.path.basename(path)
    d = os.path.dirname(path) or "."
    matches = glob.glob(os.path.join(d, f"{base}.*.archive"))
    return sorted(matches, key=lambda p: os.path.getmtime(p))


def enforce_retention(
    path: str,
    *,
    max_archives: int | None = None,
    max_age_seconds: float | None = None,
    now: float | None = None,
) -> list[str]:
    """Prune sealed archives by count and/or age. Returns the removed paths.

    ``max_archives`` keeps only the N newest; ``max_age_seconds`` deletes archives
    older than the cutoff (``now`` injected for deterministic tests). The active
    log is never touched. Both bounds are optional; with neither set this is a
    no-op (audit data is precious — pruning is always explicit).
    """
    archives = list_archives(path)
    removed: list[str] = []

    def _rm(p: str) -> None:
        try:
            os.remove(p)
            removed.append(p)
        except OSError:
            pass

    if max_age_seconds is not None and now is not None:
        cutoff = now - max_age_seconds
        for p in list(archives):
            if os.path.getmtime(p) < cutoff:
                _rm(p)
        archives = [p for p in archives if p not in removed]

    if max_archives is not None and len(archives) > max_archives:
        for p in archives[: len(archives) - max_archives]:  # oldest first
            _rm(p)

    return removed
