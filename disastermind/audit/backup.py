"""Audit-trail backup & restore (PRD Step 9).

A tamper-proof trail is only useful if it survives the loss of its host. This
module snapshots the full audit estate — the active hash-chained log, every
sealed ``*.archive`` from :mod:`audit.retention`, and the detached ``*.sig``
signatures from :mod:`audit.signing` — into a single timestamped backup
directory, with a JSON manifest recording exactly what was copied. Restore
reverses it, and :func:`verify_backup` re-checks every signed file so you know a
backup is trustworthy *before* you rely on it.

Stdlib-only (``shutil``/``os``/``json`` + :mod:`audit.signing`'s ``hmac``), no
network, deterministic — the caller supplies the ``stamp`` (no wall-clock here),
mirroring :func:`audit.retention.rotate_audit_log`.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field

from .retention import list_archives
from .signing import SIG_SUFFIX, sig_path, verify_signed

MANIFEST_NAME = "manifest.json"


@dataclass
class BackupManifest:
    stamp: str
    src_path: str
    log: str | None  # backed-up basename of the active log, or None if absent
    archives: list[str] = field(default_factory=list)  # archive basenames
    signatures: list[str] = field(default_factory=list)  # .sig basenames

    @property
    def files(self) -> list[str]:
        out: list[str] = []
        if self.log:
            out.append(self.log)
        out.extend(self.archives)
        out.extend(self.signatures)
        return out


def _copy_if_exists(src: str, dest_dir: str) -> str | None:
    """Copy ``src`` into ``dest_dir`` (metadata preserved); return basename or None."""
    if not os.path.exists(src):
        return None
    base = os.path.basename(src)
    shutil.copy2(src, os.path.join(dest_dir, base))
    return base


def backup_audit(src_path: str, dest_dir: str, *, stamp: str) -> BackupManifest:
    """Snapshot the audit estate for ``src_path`` into ``dest_dir/<stamp>/``.

    Copies the active log (if present), all sealed archives, and any detached
    ``.sig`` files for the log and each archive, preserving file metadata. Writes
    a :data:`MANIFEST_NAME` describing the snapshot and returns the
    :class:`BackupManifest`. ``stamp`` is caller-supplied for determinism.
    """
    backup_dir = os.path.join(dest_dir, stamp)
    os.makedirs(backup_dir, exist_ok=True)

    log = _copy_if_exists(src_path, backup_dir)

    archives: list[str] = []
    signatures: list[str] = []

    # Active log's signature.
    log_sig = _copy_if_exists(sig_path(src_path), backup_dir)
    if log_sig:
        signatures.append(log_sig)

    # Each sealed archive plus its signature.
    for arch in list_archives(src_path):
        a = _copy_if_exists(arch, backup_dir)
        if a:
            archives.append(a)
        s = _copy_if_exists(sig_path(arch), backup_dir)
        if s:
            signatures.append(s)

    manifest = BackupManifest(
        stamp=stamp, src_path=src_path, log=log, archives=archives, signatures=signatures
    )
    with open(os.path.join(backup_dir, MANIFEST_NAME), "w", encoding="utf-8") as fh:
        json.dump(asdict(manifest), fh, sort_keys=True, indent=2)
    return manifest


def read_manifest(backup_dir: str) -> BackupManifest:
    """Load the :class:`BackupManifest` written by :func:`backup_audit`."""
    with open(os.path.join(backup_dir, MANIFEST_NAME), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return BackupManifest(
        stamp=data["stamp"],
        src_path=data["src_path"],
        log=data.get("log"),
        archives=list(data.get("archives", [])),
        signatures=list(data.get("signatures", [])),
    )


def restore_audit(backup_dir: str, dest_path: str) -> list[str]:
    """Restore a backup made by :func:`backup_audit` to ``dest_path``.

    The active log is restored to ``dest_path``; archives and ``.sig`` files are
    restored alongside it (same directory), reproducing the original layout so
    :func:`audit.retention.list_archives` and :func:`audit.signing.verify_signed`
    work unchanged against the restored estate. Returns the restored paths.

    Archive/signature basenames embed the original log's basename (e.g.
    ``audit.jsonl.<stamp>.archive``). If ``dest_path`` has a different basename
    than the original source, those names are rewritten so the restored estate
    stays internally consistent.
    """
    manifest = read_manifest(backup_dir)
    dest_dir = os.path.dirname(dest_path) or "."
    os.makedirs(dest_dir, exist_ok=True)

    old_base = os.path.basename(manifest.src_path)
    new_base = os.path.basename(dest_path)
    restored: list[str] = []

    def _target(name: str) -> str:
        # Rewrite the embedded log basename when restoring under a new name so
        # that <log>.<stamp>.archive and <name>.sig keep referring to the
        # restored log rather than the original source.
        if old_base and new_base and name.startswith(old_base):
            name = new_base + name[len(old_base):]
        return os.path.join(dest_dir, name)

    # Active log first (named exactly dest_path).
    if manifest.log:
        src = os.path.join(backup_dir, manifest.log)
        shutil.copy2(src, dest_path)
        restored.append(dest_path)

    for name in manifest.archives + manifest.signatures:
        src = os.path.join(backup_dir, name)
        if not os.path.exists(src):
            continue
        tgt = _target(name)
        shutil.copy2(src, tgt)
        restored.append(tgt)

    return restored


def verify_backup(backup_dir: str, secret: str | bytes | None = None) -> bool:
    """Return True iff every signed file in the backup verifies under ``secret``.

    Walks the manifest's ``.sig`` files; for each, confirms the file it signs is
    present in the backup and that :func:`audit.signing.verify_signed` passes
    against the in-backup copy. A backup with no signatures fails closed (False) —
    there is nothing to attest, so it cannot be trusted. Never raises.
    """
    try:
        manifest = read_manifest(backup_dir)
    except (OSError, ValueError, KeyError):
        return False

    if not manifest.signatures:
        return False

    for sig_name in manifest.signatures:
        if not sig_name.endswith(SIG_SUFFIX):
            return False
        signed_name = sig_name[: -len(SIG_SUFFIX)]
        signed_in_backup = os.path.join(backup_dir, signed_name)
        if not os.path.exists(signed_in_backup):
            return False
        if not os.path.exists(os.path.join(backup_dir, sig_name)):
            return False
        if not verify_signed(signed_in_backup, secret):
            return False
    return True
