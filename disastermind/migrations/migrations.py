"""Versioned database migration runner (PRD Step 9 — durable schema bring-up).

The persistence layer (``disastermind.persistence``) writes resource state to
PostGIS and telemetry to a TimescaleDB hypertable, but only the *repositories*
know that SQL — nothing was responsible for *creating* the schema on a fresh
deployment. This module fills that gap: it applies the canonical DDL from
:mod:`disastermind.integrations.sql` in a deterministic, versioned, idempotent
order and records what ran in a ``schema_migrations`` table so re-runs are safe.

Design (mirrors the rest of the codebase — stdlib-only import path, optional
deps lazy + guarded, fully offline-safe):

* **OFFLINE-SAFE.** With no durable backend requested (``DM_PERSIST`` unset) or
  no Postgres/Timescale DSN / no ``psycopg`` driver, :func:`apply_migrations`
  performs a clean **dry-run**: it returns the list of migrations that *would*
  run and opens **no connection** (PRD Step 10 graceful degradation). The same
  happens when ``dry_run=True`` is passed explicitly.
* **LIVE.** When ``DM_PERSIST`` (or ``DM_LIVE``) is set *and* a usable DSN is
  configured *and* ``psycopg`` imports, it connects, ensures the
  ``schema_migrations`` ledger exists, then for each not-yet-applied migration
  runs its DDL statements and inserts a ledger row — all inside one
  transaction, committed at the end. Already-applied versions are skipped, so
  re-running is a no-op.

This module is **additive**: it imports the DDL builders from
:mod:`disastermind.integrations.sql` but does not modify ``storage`` or
``integrations`` — the existing repositories are unchanged.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..core.config import Settings
from ..integrations import sql as _sql

log = logging.getLogger("disastermind.migrations")

# The ledger table that records which migrations have been applied. Kept simple
# and ``IF NOT EXISTS`` so bootstrapping it is itself idempotent.
SCHEMA_MIGRATIONS_TABLE = "schema_migrations"

CREATE_SCHEMA_MIGRATIONS_DDL = (
    f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (\n"
    "    version     TEXT PRIMARY KEY,\n"
    "    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()\n"
    ");"
)


# --------------------------------------------------------------------------- model
@dataclass(frozen=True)
class Migration:
    """A single ordered migration: a stable ``version`` id + its DDL program.

    ``statements`` is the ordered list of SQL strings to execute; they are run
    in order inside the surrounding transaction. ``version`` is what gets
    recorded in :data:`SCHEMA_MIGRATIONS_TABLE`, so it must never change once
    shipped (append new migrations instead).
    """

    version: str
    description: str
    statements: tuple[str, ...] = field(default_factory=tuple)


def _build_migrations() -> tuple[Migration, ...]:
    """The canonical, ordered migration list (single source of truth).

    Every DDL statement is sourced from :mod:`disastermind.integrations.sql` so
    the migrated schema is byte-for-byte what the repositories expect. Splitting
    it into logical migrations (extensions -> spatial -> telemetry) keeps each
    unit small and individually recorded, while the order still satisfies the
    dependencies (PostGIS before geometry columns, table before hypertable).
    """
    return (
        Migration(
            version="0001_postgis_extension",
            description="Enable the PostGIS extension.",
            statements=(_sql.create_postgis_extension_ddl(),),
        ),
        Migration(
            version="0002_assets",
            description="Spatial asset table + GiST geometry index.",
            statements=(
                _sql.create_assets_table_ddl(),
                _sql.create_assets_geom_index_ddl(),
            ),
        ),
        Migration(
            version="0003_zones",
            description="Population/vulnerability zone table + GiST geometry index.",
            statements=(
                _sql.create_zones_table_ddl(),
                _sql.create_zones_geom_index_ddl(),
            ),
        ),
        Migration(
            version="0004_timescale_extension",
            description="Enable the TimescaleDB extension.",
            statements=(_sql.create_timescale_extension_ddl(),),
        ),
        Migration(
            version="0005_telemetry",
            description="Telemetry table, hypertable conversion, and range index.",
            statements=(
                _sql.create_telemetry_table_ddl(),
                _sql.create_telemetry_hypertable_ddl(),
                _sql.create_telemetry_index_ddl(),
            ),
        ),
    )


# The immutable ordered program. Computed once at import (pure string building,
# no I/O, no connection).
MIGRATIONS: tuple[Migration, ...] = _build_migrations()


# ----------------------------------------------------------------------- helpers
def all_migrations() -> list[Migration]:
    """Return the full ordered migration list (a fresh copy for safe iteration)."""
    return list(MIGRATIONS)


def _persist_requested() -> bool:
    """True when durable backends are requested via ``DM_PERSIST``/``DM_LIVE``.

    Mirrors :func:`disastermind.persistence.build._persist_live` exactly so the
    migration runner and the persistor agree on what "durable" means, without
    importing it (this package must not depend on persistence wiring).
    """
    for key in ("DM_PERSIST", "DM_LIVE"):
        val = os.environ.get(key)
        if val is not None and val.strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _dsn_for(settings: Settings) -> str:
    """Pick the DSN to migrate against (Postgres/Timescale share a PostGIS DB).

    Prefer ``postgres_dsn`` (the resource/PostGIS store, which carries the
    spatial schema); fall back to ``timescale_dsn``. Returns ``""`` when neither
    is meaningfully configured.
    """
    for dsn in (settings.postgres_dsn, settings.timescale_dsn):
        if _is_configured_dsn(dsn):
            return dsn
    return ""


def _is_configured_dsn(dsn: str | None) -> bool:
    """True when ``dsn`` looks like a real, intentionally-configured Postgres DSN.

    The default :class:`Settings` carry a ``postgresql://localhost/...`` placeholder
    so the package imports cleanly; that placeholder must NOT, on its own, trigger
    a live connection attempt. We therefore treat the bare localhost defaults as
    "unconfigured" and require an explicit host/credentials (or the env var to be
    set) for a live apply. This keeps the default-Settings path a guaranteed
    no-network dry-run.
    """
    if not dsn or not dsn.strip():
        return False
    text = dsn.strip()
    if not (text.startswith("postgres://") or text.startswith("postgresql://")):
        return False
    # The literal localhost defaults from core.config.Settings are placeholders.
    placeholders = {
        "postgresql://localhost/disastermind",
        "postgresql://localhost/dm_telemetry",
    }
    if text in placeholders:
        return False
    return True


def can_apply(settings: Settings | None = None) -> bool:
    """True only when a *live* apply is both requested and possible.

    Requires (a) ``DM_PERSIST``/``DM_LIVE`` set, and (b) a configured DSN. The
    driver itself is checked lazily at connect time; if it is missing we fall
    back to a dry-run rather than raising.
    """
    s = settings or Settings()
    return _persist_requested() and bool(_dsn_for(s))


# --------------------------------------------------------------------- live access
def _connect(dsn: str):  # pragma: no cover - optional dependency / real network
    """Open a psycopg connection (lazy import + guarded), or return ``None``.

    Mirrors the connection style used by the storage repos: ``psycopg`` imported
    inside the function, a short ``connect_timeout``, and any failure degrades to
    ``None`` (caller then dry-runs) rather than raising.
    """
    try:
        import psycopg  # type: ignore
    except Exception:
        log.warning("psycopg unavailable; migrations dry-run only")
        return None
    try:
        return psycopg.connect(dsn, connect_timeout=5)
    except Exception:
        log.warning("could not connect to %s; migrations dry-run only", _redact(dsn))
        return None


def _ensure_ledger(conn) -> None:  # pragma: no cover - live path
    """Create the ``schema_migrations`` ledger table if it does not exist."""
    with conn.cursor() as cur:
        cur.execute(CREATE_SCHEMA_MIGRATIONS_DDL)


def applied_versions(conn) -> set[str]:
    """Return the set of migration versions already recorded in the ledger.

    Creates the ledger table first (idempotent) so this is safe to call on a
    brand-new database. Works against any DB-API connection exposing
    ``cursor()`` — so tests can pass a fake connection.
    """
    with conn.cursor() as cur:
        cur.execute(CREATE_SCHEMA_MIGRATIONS_DDL)
        cur.execute(f"SELECT version FROM {SCHEMA_MIGRATIONS_TABLE}")
        rows = cur.fetchall() or []
    return {str(r[0]) for r in rows}


def pending_migrations(
    applied: set[str] | None = None,
    *,
    migrations: tuple[Migration, ...] | None = None,
) -> list[Migration]:
    """Ordered migrations not present in ``applied`` (defaults to *all* of them).

    Pure/offline: with ``applied=None`` (or empty) it returns the whole ordered
    program, which is exactly what the dry-run report lists. No connection.
    """
    done = applied or set()
    src = migrations if migrations is not None else MIGRATIONS
    return [m for m in src if m.version not in done]


def _record_applied(conn, version: str, *, now: datetime | None = None) -> None:  # pragma: no cover - live path
    """Insert a ledger row marking ``version`` as applied (idempotent)."""
    ts = (now or datetime.now(timezone.utc)).isoformat()
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} (version, applied_at) "
            "VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
            (version, ts),
        )


# ------------------------------------------------------------------- public report
def _report(
    *,
    mode: str,
    pending: list[Migration],
    applied: list[str],
    dsn: str = "",
    error: str | None = None,
) -> dict[str, Any]:
    """Build the structured report returned by :func:`apply_migrations`."""
    return {
        "mode": mode,  # "dry-run" | "applied"
        "dry_run": mode != "applied",
        "dsn": _redact(dsn),
        "total": len(MIGRATIONS),
        "pending": [m.version for m in pending],
        "pending_count": len(pending),
        "applied": list(applied),
        "applied_count": len(applied),
        "error": error,
    }


def apply_migrations(
    settings: Settings | None = None,
    *,
    dry_run: bool = False,
    connect: Callable[[str], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ensure the durable schema exists and is versioned; return a report dict.

    Behaviour:

    * **Dry-run** (``dry_run=True``, or no live backend requested/configured, or
      the driver/connection is unavailable): opens **no connection** and returns
      a report whose ``pending`` lists every migration that *would* run.
    * **Live**: connects (via ``connect`` or the lazy psycopg helper), ensures
      the ``schema_migrations`` ledger, and for each not-yet-applied migration
      executes its DDL and records it — all in one transaction, committed at the
      end. Already-applied versions are skipped (idempotent). On any error the
      transaction is rolled back and the report carries ``error``.

    ``connect``/``now`` are injection seams for deterministic testing; production
    callers leave them ``None``.

    The report is a plain ``dict`` (JSON-serialisable) so the CLI and any HTTP
    surface can render it directly.
    """
    s = settings or Settings()

    # Decide whether a live apply is even on the table.
    want_live = (not dry_run) and (connect is not None or can_apply(s))
    if not want_live:
        # Pure offline dry-run: list everything that would run, touch nothing.
        return _report(mode="dry-run", pending=list(MIGRATIONS), applied=[])

    dsn = _dsn_for(s)
    opener = connect or _connect
    conn = opener(dsn)
    if conn is None:
        # Requested live but the backend/driver is unreachable -> safe dry-run.
        return _report(mode="dry-run", pending=list(MIGRATIONS), applied=[], dsn=dsn)

    newly_applied: list[str] = []
    try:
        _ensure_ledger(conn)
        done = applied_versions(conn)
        pending = pending_migrations(done)
        for migration in pending:
            with conn.cursor() as cur:
                for stmt in migration.statements:
                    cur.execute(stmt)
            _record_applied(conn, migration.version, now=now)
            newly_applied.append(migration.version)
        conn.commit()
    except Exception as exc:  # pragma: no cover - live error path
        try:
            conn.rollback()
        except Exception:
            log.exception("rollback failed after migration error")
        log.exception("migration apply failed")
        # Report the failure rather than raising so the CLI degrades gracefully.
        return _report(
            mode="dry-run",
            pending=pending_migrations(set(newly_applied)),
            applied=newly_applied,
            dsn=dsn,
            error=str(exc),
        )
    finally:
        try:
            conn.close()
        except Exception:
            log.exception("error closing migration connection")

    return _report(
        mode="applied",
        pending=pending_migrations(set(newly_applied) | _safe_done(done)),
        applied=newly_applied,
        dsn=dsn,
    )


def _safe_done(done: Any) -> set[str]:
    """Coerce a possibly-None applied set to a real set."""
    return set(done) if done else set()


def _redact(dsn: str | None) -> str:
    """Strip any ``user:password@`` credentials from a DSN before logging/reporting."""
    if not dsn:
        return ""
    text = str(dsn)
    if "@" not in text or "://" not in text:
        return text
    scheme, rest = text.split("://", 1)
    if "@" in rest:
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return text


def format_report(report: dict[str, Any]) -> str:
    """Render a report dict as a short human-readable summary for the CLI."""
    lines = [
        f"DisasterMind migrations [{report['mode']}]",
        f"  total migrations : {report['total']}",
        f"  pending          : {report['pending_count']}",
    ]
    if report.get("dsn"):
        lines.append(f"  target           : {report['dsn']}")
    if report["dry_run"]:
        lines.append("  (no database connection was opened)")
        for v in report["pending"]:
            lines.append(f"    would apply: {v}")
    else:
        lines.append(f"  newly applied    : {report['applied_count']}")
        for v in report["applied"]:
            lines.append(f"    applied: {v}")
        if not report["applied"]:
            lines.append("    (schema already up to date)")
    if report.get("error"):
        lines.append(f"  ERROR: {report['error']}")
    return "\n".join(lines)
