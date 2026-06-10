"""Versioned DB migration runner for the DisasterMind durable schema (PRD Step 9).

Public surface::

    from disastermind.migrations import apply_migrations, pending_migrations

``apply_migrations(settings=None, *, dry_run=False)`` ensures the PostGIS +
TimescaleDB schema (sourced from :mod:`disastermind.integrations.sql`) exists and
is recorded in a ``schema_migrations`` ledger, idempotently. It is OFFLINE-SAFE:
with no durable backend requested/configured it is a clean dry-run that opens no
connection and merely lists the migrations that would run. Run as a tool with
``python -m disastermind.migrations [--dry-run]``.
"""
from __future__ import annotations

from .migrations import (
    CREATE_SCHEMA_MIGRATIONS_DDL,
    MIGRATIONS,
    SCHEMA_MIGRATIONS_TABLE,
    Migration,
    all_migrations,
    applied_versions,
    apply_migrations,
    can_apply,
    format_report,
    pending_migrations,
)

__all__ = [
    "CREATE_SCHEMA_MIGRATIONS_DDL",
    "MIGRATIONS",
    "SCHEMA_MIGRATIONS_TABLE",
    "Migration",
    "all_migrations",
    "applied_versions",
    "apply_migrations",
    "can_apply",
    "format_report",
    "pending_migrations",
]
