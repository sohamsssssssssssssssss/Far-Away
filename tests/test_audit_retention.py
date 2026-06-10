"""Audit retention/rotation tests (PRD Step 9).

Rotation must preserve the tamper-evident hash chain: the sealed archive stays
fully verifiable, and the fresh log starts a new valid chain. Retention prunes
old archives by count/age, never the active log. Deterministic (injected stamp +
``now``), stdlib-only, no network.
"""
from __future__ import annotations

import os

from disastermind.audit.decision_log import DecisionLogger
from disastermind.audit.retention import (
    enforce_retention,
    list_archives,
    rotate_audit_log,
)
from disastermind.core.contracts import Message, MessageType, Priority, Topic


def _log_some(path: str, n: int) -> DecisionLogger:
    lg = DecisionLogger(path=path)
    for i in range(n):
        lg.record(
            Message(sender="a", recipient="b", type=MessageType.ALERT,
                    priority=Priority.INFO, topic=Topic.RAW_FEED, payload={"i": i})
        )
    return lg


def test_under_threshold_does_not_rotate(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 3)
    res = rotate_audit_log(p, max_bytes=10_000_000)
    assert res.rotated is False
    assert not list_archives(p)


def test_rotation_seals_a_verifiable_chain_and_starts_fresh(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 5)
    res = rotate_audit_log(p, force=True, stamp="20260610T0000")
    assert res.rotated is True and res.archived_path and os.path.exists(res.archived_path)
    # the sealed archive still verifies as an intact hash chain
    assert DecisionLogger(path=res.archived_path).verify_chain() is True
    # a fresh empty active log exists and is valid; appending continues cleanly
    assert os.path.exists(p) and os.path.getsize(p) == 0
    lg2 = _log_some(p, 2)
    assert lg2.verify_chain() is True


def test_enforce_retention_by_count(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    for k in range(4):
        _log_some(p, 2)
        rotate_audit_log(p, force=True, stamp=f"stamp{k}")
    assert len(list_archives(p)) == 4
    removed = enforce_retention(p, max_archives=2)
    assert len(removed) == 2
    assert len(list_archives(p)) == 2  # only the 2 newest kept


def test_enforce_retention_by_age(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 1)
    res = rotate_audit_log(p, force=True, stamp="old")
    # age the archive far into the past
    old = res.archived_path
    os.utime(old, (1000.0, 1000.0))
    removed = enforce_retention(p, max_age_seconds=100.0, now=10_000.0)
    assert old in removed and not os.path.exists(old)


def test_no_bounds_is_noop(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 1)
    rotate_audit_log(p, force=True, stamp="s")
    assert enforce_retention(p) == []  # pruning is always explicit
