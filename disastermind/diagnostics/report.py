"""Diagnostic result model + renderers.

A :class:`Report` is a bag of :class:`Check` results plus convenience renderers
(dict / Markdown / POSIX exit code). It is stdlib-only and entirely
deterministic so it is safe to assert against in tests.

Severity model
--------------
Every individual probe yields a :class:`Check` with a :class:`Status`:

  * ``OK``    — the probe passed.
  * ``WARN``  — non-fatal: degraded but the system can still coordinate
                (e.g. an optional backend is unreachable). Does NOT fail the run.
  * ``FAIL``  — a real defect (broken module, imbalanced DAG, bad config,
                tampered audit chain). Fails the run (non-zero exit code).
  * ``SKIP``  — the probe could not run (optional dependency absent). Neutral.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


#: rank for "worst status wins" roll-ups (higher == more severe)
_RANK = {Status.OK: 0, Status.SKIP: 0, Status.WARN: 1, Status.FAIL: 2}

_ICON = {Status.OK: "✅", Status.WARN: "⚠️", Status.FAIL: "❌", Status.SKIP: "➖"}


@dataclass
class Check:
    """One diagnostic probe result."""

    name: str
    status: Status
    detail: str = ""
    #: arbitrary structured evidence (subscriber lists, counts, ...)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (Status.OK, Status.SKIP)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "data": self.data,
        }


@dataclass
class Report:
    """Aggregate of all diagnostic checks for one ``run_diagnostics`` call."""

    checks: list[Check] = field(default_factory=list)
    #: free-form metadata (counts, degraded modules, environment notes)
    meta: dict[str, Any] = field(default_factory=dict)

    # ----------------------------------------------------------- construction
    def add(
        self,
        name: str,
        status: Status,
        detail: str = "",
        data: dict[str, Any] | None = None,
    ) -> Check:
        chk = Check(name=name, status=status, detail=detail, data=data or {})
        self.checks.append(chk)
        return chk

    # ------------------------------------------------------------- roll-ups
    @property
    def status(self) -> Status:
        """Worst status across every check (empty report == OK)."""
        worst = Status.OK
        for chk in self.checks:
            if _RANK[chk.status] > _RANK[worst]:
                worst = chk.status
        return worst

    @property
    def ok(self) -> bool:
        """True iff no check FAILED (warnings/skips do not fail the run)."""
        return all(chk.ok or chk.status is Status.WARN for chk in self.checks)

    @property
    def healthy(self) -> bool:
        """Strict health: every check OK or SKIP (no warnings, no failures)."""
        return all(chk.ok for chk in self.checks)

    @property
    def exit_code(self) -> int:
        """POSIX exit code: 0 when nothing FAILED, 1 otherwise."""
        return 0 if self.ok else 1

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Status}
        for chk in self.checks:
            out[chk.status.value] += 1
        return out

    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.FAIL]

    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.WARN]

    # -------------------------------------------------------------- renderers
    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "ok": self.ok,
            "healthy": self.healthy,
            "exit_code": self.exit_code,
            "counts": self.counts(),
            "checks": [c.to_dict() for c in self.checks],
            "meta": self.meta,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_markdown(self) -> str:
        lines: list[str] = []
        head = _ICON[self.status]
        lines.append(f"# DisasterMind doctor — {head} {self.status.value.upper()}")
        c = self.counts()
        lines.append("")
        lines.append(
            f"**{c['ok']} ok · {c['warn']} warn · {c['fail']} fail · "
            f"{c['skip']} skip** — exit code `{self.exit_code}`"
        )
        lines.append("")
        lines.append("| | check | detail |")
        lines.append("|---|---|---|")
        for chk in self.checks:
            detail = (chk.detail or "").replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {_ICON[chk.status]} | {chk.name} | {detail} |")
        if self.meta:
            lines.append("")
            lines.append("## Details")
            for key, val in self.meta.items():
                lines.append(f"- **{key}**: {val}")
        lines.append("")
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.to_markdown()
