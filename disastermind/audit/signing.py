"""Tamper-PROOF audit signing (PRD Step 9).

The decision log (:mod:`disastermind.audit.decision_log`) is a hash-chained
JSONL file — tamper-*evident*: any retroactive edit breaks the chain. But the
chain is self-contained, so an attacker who can rewrite the whole file can also
recompute every ``_hash`` and forge a perfectly valid chain. The fix is a secret
the attacker does not have: an HMAC keyed by a server-side audit secret. Without
the key you cannot produce a matching signature, so a full-file rewrite is
detectable — that is what makes the trail tamper-*proof*.

This is a **detached** layer: the signature lives in a sibling ``<path>.sig``
file and is computed over the raw file bytes. It does NOT touch
:mod:`decision_log` or :mod:`retention`; it composes with both — sign the active
log, or sign a sealed ``*.archive`` after rotation. Offline, stdlib-only
(``hmac``/``hashlib``), no network, deterministic.

The secret is resolved from the explicit ``secret`` argument or, failing that,
the ``DM_AUDIT_SECRET`` environment variable. Operators should set a strong
random value and keep it off the audited host where possible.
"""
from __future__ import annotations

import hashlib
import hmac
import os

SIG_SUFFIX = ".sig"
_ENV_VAR = "DM_AUDIT_SECRET"
_CHUNK = 1 << 20  # 1 MiB streaming reads keep memory flat for big logs


class AuditSecretMissing(RuntimeError):
    """Raised when no signing secret can be resolved (arg or env)."""


def resolve_secret(secret: str | bytes | None = None) -> bytes:
    """Resolve the signing secret from ``secret`` or ``DM_AUDIT_SECRET``.

    Returns the secret as bytes. Raises :class:`AuditSecretMissing` when neither
    source yields a non-empty value — we never sign with an empty key, which
    would defeat the whole point.
    """
    if secret is None:
        secret = os.environ.get(_ENV_VAR, "")
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not secret:
        raise AuditSecretMissing(
            f"no audit secret provided (pass secret= or set {_ENV_VAR})"
        )
    return secret


def sig_path(path: str) -> str:
    """The detached signature path for ``path`` (``<path>.sig``)."""
    return path + SIG_SUFFIX


def _hexdigest(path: str, secret: bytes) -> str:
    """HMAC-SHA256 over the raw bytes of ``path``, streamed in chunks."""
    mac = hmac.new(secret, digestmod=hashlib.sha256)
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            mac.update(chunk)
    return mac.hexdigest()


def compute_signature(path: str, secret: str | bytes | None = None) -> str:
    """Return the HMAC-SHA256 hex signature of ``path`` (no file written)."""
    key = resolve_secret(secret)
    return _hexdigest(path, key)


def sign_log(path: str, secret: str | bytes | None = None) -> str:
    """Sign ``path`` and write a DETACHED ``<path>.sig`` file.

    The signature is the HMAC-SHA256 (keyed by the resolved secret) over the
    file's raw bytes, written as a single hex line. Returns the signature path.
    Overwrites any prior signature (re-signing after appends is expected).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    digest = compute_signature(path, secret)
    out = sig_path(path)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(digest + "\n")
    return out


def verify_signed(path: str, secret: str | bytes | None = None) -> bool:
    """Return True iff ``path`` is unmodified since it was signed.

    Reads the detached ``<path>.sig``, recomputes the HMAC with the resolved
    secret, and compares in constant time (:func:`hmac.compare_digest`). Returns
    False — never raises — when the file or its signature is missing, malformed,
    tampered, or signed under a different secret.
    """
    if not os.path.exists(path):
        return False
    sig = sig_path(path)
    if not os.path.exists(sig):
        return False
    try:
        with open(sig, "r", encoding="utf-8") as fh:
            stored = fh.read().strip()
        expected = compute_signature(path, secret)
    except (OSError, AuditSecretMissing):
        return False
    if not stored:
        return False
    return hmac.compare_digest(stored, expected)


# --------------------------------------------------------------------- archives
# Convenience wrappers over the sealed *.archive files produced by
# audit.retention.rotate_audit_log. A sealed archive is just a regular file, so
# these are thin aliases that make intent explicit at call sites.

def sign_archive(archive_path: str, secret: str | bytes | None = None) -> str:
    """Sign a sealed ``*.archive`` from :mod:`audit.retention`. See :func:`sign_log`."""
    return sign_log(archive_path, secret)


def verify_archive(archive_path: str, secret: str | bytes | None = None) -> bool:
    """Verify a sealed ``*.archive``. See :func:`verify_signed`."""
    return verify_signed(archive_path, secret)
