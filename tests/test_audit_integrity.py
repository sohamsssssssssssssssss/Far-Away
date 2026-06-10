"""Tamper-PROOF audit integrity tests (PRD Step 9).

The hash chain (decision_log) is tamper-*evident* but self-contained: a
full-file rewrite recomputes it. The HMAC signing layer (audit.signing) keys the
attestation with a server-side secret an attacker lacks, making the trail
tamper-*proof*. Backup (audit.backup) snapshots and restores the whole estate —
active log + sealed archives + their signatures — and re-verifies it.

Deterministic (injected stamp + explicit secrets), stdlib-only, no network.
"""
from __future__ import annotations

import os

import pytest

from disastermind.audit.backup import (
    backup_audit,
    read_manifest,
    restore_audit,
    verify_backup,
)
from disastermind.audit.decision_log import DecisionLogger
from disastermind.audit.retention import list_archives, rotate_audit_log
from disastermind.audit.signing import (
    AuditSecretMissing,
    resolve_secret,
    sig_path,
    sign_archive,
    sign_log,
    verify_archive,
    verify_signed,
)
from disastermind.core.contracts import Message, MessageType, Priority, Topic

SECRET = "super-secret-audit-key"
WRONG = "not-the-key"


def _log_some(path: str, n: int) -> DecisionLogger:
    lg = DecisionLogger(path=path)
    for i in range(n):
        lg.record(
            Message(sender="a", recipient="b", type=MessageType.ALERT,
                    priority=Priority.INFO, topic=Topic.RAW_FEED, payload={"i": i})
        )
    return lg


def _flip_one_byte(path: str) -> None:
    with open(path, "rb") as fh:
        data = bytearray(fh.read())
    assert data, "cannot tamper an empty file"
    # Flip a byte well inside the file so the chain bytes change.
    idx = len(data) // 2
    data[idx] ^= 0xFF
    with open(path, "wb") as fh:
        fh.write(data)


# --------------------------------------------------------------------- signing
def test_sign_then_verify_true(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 5)
    out = sign_log(p, SECRET)
    assert out == sig_path(p) and os.path.exists(out)
    assert verify_signed(p, SECRET) is True


def test_flip_one_byte_fails_verification(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 5)
    sign_log(p, SECRET)
    assert verify_signed(p, SECRET) is True
    _flip_one_byte(p)
    assert verify_signed(p, SECRET) is False


def test_full_file_rewrite_is_detected(tmp_path):
    # The whole point: an attacker recomputes a *valid* hash chain, but without
    # the secret the HMAC no longer matches.
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 4)
    sign_log(p, SECRET)
    # Forge a brand-new but internally-valid chain in place of the original.
    forged = str(tmp_path / "forged.jsonl")
    _log_some(forged, 4)
    with open(forged, "rb") as fh:
        forged_bytes = fh.read()
    with open(p, "wb") as fh:
        fh.write(forged_bytes)
    # The forged chain verifies on its own (tamper-evident is not enough)...
    assert DecisionLogger(path=p).verify_chain() is True
    # ...but the HMAC signature does not (tamper-proof).
    assert verify_signed(p, SECRET) is False


def test_wrong_secret_fails(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 3)
    sign_log(p, SECRET)
    assert verify_signed(p, WRONG) is False
    assert verify_signed(p, SECRET) is True


def test_missing_signature_or_file_returns_false(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 2)
    # No .sig written yet.
    assert verify_signed(p, SECRET) is False
    # Missing data file.
    assert verify_signed(str(tmp_path / "nope.jsonl"), SECRET) is False


def test_secret_resolution_from_env(tmp_path, monkeypatch):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 2)
    monkeypatch.setenv("DM_AUDIT_SECRET", SECRET)
    sign_log(p)  # secret pulled from env
    assert verify_signed(p) is True  # also from env
    monkeypatch.setenv("DM_AUDIT_SECRET", WRONG)
    assert verify_signed(p) is False


def test_missing_secret_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("DM_AUDIT_SECRET", raising=False)
    with pytest.raises(AuditSecretMissing):
        resolve_secret(None)
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 1)
    with pytest.raises(AuditSecretMissing):
        sign_log(p)


def test_resolve_secret_accepts_str_and_bytes():
    assert resolve_secret("k") == b"k"
    assert resolve_secret(b"k") == b"k"


# -------------------------------------------------------------- sealed archives
def test_sealed_archive_round_trips_sign_verify(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 5)
    res = rotate_audit_log(p, force=True, stamp="20260610T0000")
    archive = res.archived_path
    assert archive and os.path.exists(archive)
    # The sealed archive still verifies as a hash chain (retention guarantee)...
    assert DecisionLogger(path=archive).verify_chain() is True
    # ...and now also under HMAC signing.
    sign_archive(archive, SECRET)
    assert verify_archive(archive, SECRET) is True
    assert verify_archive(archive, WRONG) is False
    _flip_one_byte(archive)
    assert verify_archive(archive, SECRET) is False


# ----------------------------------------------------------------- backup pipe
def _build_estate(tmp_path):
    """Active log + one sealed archive, all signed. Returns the log path."""
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 4)
    res = rotate_audit_log(p, force=True, stamp="arch1")
    sign_archive(res.archived_path, SECRET)
    _log_some(p, 3)  # fresh active chain
    sign_log(p, SECRET)
    return p


def test_backup_copies_log_archives_and_sigs(tmp_path):
    p = _build_estate(tmp_path)
    dest = str(tmp_path / "backups")
    manifest = backup_audit(p, dest, stamp="B1")
    backup_dir = os.path.join(dest, "B1")

    assert manifest.log == "audit.jsonl"
    assert manifest.archives == ["audit.jsonl.arch1.archive"]
    # Two signatures: one for the active log, one for the archive.
    assert set(manifest.signatures) == {
        "audit.jsonl.sig",
        "audit.jsonl.arch1.archive.sig",
    }
    for name in manifest.files:
        assert os.path.exists(os.path.join(backup_dir, name))
    # Manifest persisted and reloadable.
    assert read_manifest(backup_dir).stamp == "B1"


def test_restore_reproduces_estate(tmp_path):
    p = _build_estate(tmp_path)
    dest = str(tmp_path / "backups")
    backup_audit(p, dest, stamp="B1")
    backup_dir = os.path.join(dest, "B1")

    out = str(tmp_path / "restored" / "audit.jsonl")
    restored = restore_audit(backup_dir, out)

    assert os.path.exists(out)
    # Restored active log is a valid chain and verifies under the secret.
    assert DecisionLogger(path=out).verify_chain() is True
    assert verify_signed(out, SECRET) is True
    # The archive came back alongside and still verifies (chain + signature).
    archs = list_archives(out)
    assert len(archs) == 1
    assert DecisionLogger(path=archs[0]).verify_chain() is True
    assert verify_archive(archs[0], SECRET) is True
    assert out in restored


def test_verify_backup_passes_then_fails_after_tamper(tmp_path):
    p = _build_estate(tmp_path)
    dest = str(tmp_path / "backups")
    backup_audit(p, dest, stamp="B1")
    backup_dir = os.path.join(dest, "B1")

    assert verify_backup(backup_dir, SECRET) is True
    assert verify_backup(backup_dir, WRONG) is False

    # Tamper with a file inside the backup -> verification fails.
    _flip_one_byte(os.path.join(backup_dir, "audit.jsonl"))
    assert verify_backup(backup_dir, SECRET) is False


def test_verify_backup_no_signatures_fails_closed(tmp_path):
    # An unsigned estate produces a backup with nothing to attest.
    p = str(tmp_path / "audit.jsonl")
    _log_some(p, 3)  # no sign_log call
    dest = str(tmp_path / "backups")
    backup_audit(p, dest, stamp="B0")
    assert verify_backup(os.path.join(dest, "B0"), SECRET) is False


def test_verify_backup_missing_dir_returns_false(tmp_path):
    assert verify_backup(str(tmp_path / "nonexistent"), SECRET) is False
