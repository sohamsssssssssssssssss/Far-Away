"""Tests for the versioned DB migration runner (PRD Step 9).

Covers:
  * the ordered migration list contains the expected canonical DDL;
  * the default-Settings dry-run is a no-op that opens NO connection and lists
    every migration that would run;
  * the already-applied skip logic (idempotent re-runs);
  * a deterministic live-apply path via an injected fake DB-API connection
    (stdlib only, no network), plus an importorskip('psycopg') guard for the
    real driver path.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from disastermind.core.config import Settings
from disastermind.integrations import sql as ddl
from disastermind.migrations import (
    MIGRATIONS,
    SCHEMA_MIGRATIONS_TABLE,
    Migration,
    applied_versions,
    apply_migrations,
    can_apply,
    format_report,
    pending_migrations,
)
from disastermind.migrations import migrations as migrations_mod


# --------------------------------------------------------------- fake DB-API conn
class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn
        self._rows: list[tuple] = []

    def execute(self, stmt, params=None):
        self._conn.executed.append((stmt, params))
        # Emulate just enough of the ledger SELECT for applied_versions().
        if stmt.strip().upper().startswith("SELECT VERSION FROM"):
            self._rows = [(v,) for v in sorted(self._conn.ledger)]
        elif stmt.strip().upper().startswith(f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE.upper()}"):
            # params == (version, applied_at)
            self._conn.ledger.add(params[0])
            self._rows = []
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    """Minimal DB-API 2.0 connection recording executed SQL and ledger state."""

    def __init__(self, *, preapplied: set[str] | None = None) -> None:
        self.executed: list[tuple] = []
        self.ledger: set[str] = set(preapplied or set())
        self.committed = 0
        self.rolledback = 0
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolledback += 1

    def close(self):
        self.closed = True


# --------------------------------------------------------------- migration list
def test_ordered_list_contains_expected_ddl():
    """The migration program must source the canonical DDL, in dependency order."""
    statements = [s for m in MIGRATIONS for s in m.statements]
    expected = [
        ddl.create_postgis_extension_ddl(),
        ddl.create_assets_table_ddl(),
        ddl.create_assets_geom_index_ddl(),
        ddl.create_zones_table_ddl(),
        ddl.create_zones_geom_index_ddl(),
        ddl.create_timescale_extension_ddl(),
        ddl.create_telemetry_table_ddl(),
        ddl.create_telemetry_hypertable_ddl(),
        ddl.create_telemetry_index_ddl(),
    ]
    assert statements == expected
    # Every canonical DDL builder is represented.
    assert len(statements) == 9
    # Versions are unique, stable, and sorted in apply order.
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_migration_is_frozen_dataclass():
    m = MIGRATIONS[0]
    assert isinstance(m, Migration)
    with pytest.raises(Exception):
        m.version = "x"  # type: ignore[misc]


def test_postgis_before_geometry_and_table_before_hypertable():
    versions = [m.version for m in MIGRATIONS]
    assert versions.index("0001_postgis_extension") < versions.index("0002_assets")
    assert versions.index("0004_timescale_extension") < versions.index("0005_telemetry")


# ------------------------------------------------------------------- dry-run path
def test_default_settings_dryrun_is_noop_and_opens_no_connection(monkeypatch):
    """Default Settings, no DM_PERSIST -> dry-run listing N migrations, no connect."""
    monkeypatch.delenv("DM_PERSIST", raising=False)
    monkeypatch.delenv("DM_LIVE", raising=False)

    # Guard: _connect must never be called on the offline path.
    def _boom(dsn):
        raise AssertionError("apply_migrations opened a connection during dry-run")

    monkeypatch.setattr(migrations_mod, "_connect", _boom)

    report = apply_migrations(Settings())
    assert report["dry_run"] is True
    assert report["mode"] == "dry-run"
    assert report["pending_count"] == len(MIGRATIONS)
    assert report["pending"] == [m.version for m in MIGRATIONS]
    assert report["applied"] == []
    assert report["error"] is None


def test_explicit_dry_run_flag_never_connects_even_when_configured(monkeypatch):
    monkeypatch.setenv("DM_PERSIST", "1")
    monkeypatch.setenv("DM_POSTGRES_DSN", "postgresql://user:pw@db.example/dm")

    def _boom(dsn):
        raise AssertionError("dry_run=True must not connect")

    monkeypatch.setattr(migrations_mod, "_connect", _boom)
    report = apply_migrations(Settings(), dry_run=True)
    assert report["dry_run"] is True
    assert report["pending_count"] == len(MIGRATIONS)


def test_persist_set_but_dsn_is_localhost_default_is_dryrun(monkeypatch):
    """The bare localhost placeholder DSN must not trigger a live connection."""
    monkeypatch.setenv("DM_PERSIST", "1")
    monkeypatch.delenv("DM_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("DM_TIMESCALE_DSN", raising=False)

    def _boom(dsn):
        raise AssertionError("localhost-default DSN should stay offline")

    monkeypatch.setattr(migrations_mod, "_connect", _boom)
    assert can_apply(Settings()) is False
    report = apply_migrations(Settings())
    assert report["dry_run"] is True


# --------------------------------------------------------------- pending helper
def test_pending_migrations_full_when_none_applied():
    assert pending_migrations() == list(MIGRATIONS)
    assert pending_migrations(set()) == list(MIGRATIONS)


def test_pending_migrations_skips_applied():
    done = {"0001_postgis_extension", "0002_assets"}
    pending = pending_migrations(done)
    assert [m.version for m in pending] == [
        "0003_zones",
        "0004_timescale_extension",
        "0005_telemetry",
    ]


def test_pending_migrations_all_applied_is_empty():
    done = {m.version for m in MIGRATIONS}
    assert pending_migrations(done) == []


# ------------------------------------------------------------- applied_versions
def test_applied_versions_reads_ledger():
    conn = _FakeConn(preapplied={"0001_postgis_extension"})
    assert applied_versions(conn) == {"0001_postgis_extension"}
    # The ledger table is ensured (CREATE TABLE IF NOT EXISTS) before SELECT.
    assert any("CREATE TABLE IF NOT EXISTS" in s for s, _ in conn.executed)


# ------------------------------------------------------------ live apply (faked)
def test_live_apply_runs_all_ddl_and_records_versions():
    conn = _FakeConn()
    report = apply_migrations(Settings(), connect=lambda dsn: conn)

    assert report["mode"] == "applied"
    assert report["dry_run"] is False
    assert report["applied"] == [m.version for m in MIGRATIONS]
    assert report["pending_count"] == 0
    assert conn.committed == 1
    assert conn.closed is True
    assert conn.rolledback == 0
    # Every migration's DDL was actually executed.
    executed_sql = [s for s, _ in conn.executed]
    for m in MIGRATIONS:
        for stmt in m.statements:
            assert stmt in executed_sql
    # Ledger now holds every version.
    assert conn.ledger == {m.version for m in MIGRATIONS}


def test_live_apply_idempotent_skips_already_applied():
    preapplied = {"0001_postgis_extension", "0002_assets", "0003_zones"}
    conn = _FakeConn(preapplied=set(preapplied))
    report = apply_migrations(Settings(), connect=lambda dsn: conn)

    assert report["applied"] == ["0004_timescale_extension", "0005_telemetry"]
    # No DDL from the already-applied migrations should be re-run.
    executed_sql = [s for s, _ in conn.executed]
    assert ddl.create_assets_table_ddl() not in executed_sql
    assert ddl.create_telemetry_table_ddl() in executed_sql


def test_live_apply_second_run_is_full_noop():
    conn = _FakeConn()
    apply_migrations(Settings(), connect=lambda dsn: conn)
    # Re-run against the same (now fully-applied) connection.
    conn2 = _FakeConn(preapplied=set(conn.ledger))
    report = apply_migrations(Settings(), connect=lambda dsn: conn2)
    assert report["applied"] == []
    assert report["pending_count"] == 0
    # No DDL statements executed on the no-op run (only ledger ensure + select).
    ddl_run = [
        s for s, _ in conn2.executed
        if "CREATE EXTENSION" in s or "CREATE TABLE IF NOT EXISTS dm_" in s
    ]
    assert ddl_run == []


def test_live_apply_rolls_back_on_error():
    class _BoomConn(_FakeConn):
        def cursor(self):
            raise RuntimeError("boom")

    conn = _BoomConn()
    report = apply_migrations(Settings(), connect=lambda dsn: conn)
    assert report["error"] is not None
    assert report["dry_run"] is True  # degraded report
    assert conn.rolledback >= 1
    assert conn.closed is True


def test_connect_returning_none_degrades_to_dryrun(monkeypatch):
    monkeypatch.setenv("DM_PERSIST", "1")
    monkeypatch.setenv("DM_POSTGRES_DSN", "postgresql://user:pw@db.example/dm")
    report = apply_migrations(Settings(), connect=lambda dsn: None)
    assert report["dry_run"] is True
    assert report["pending_count"] == len(MIGRATIONS)


# ----------------------------------------------------------------- DSN redaction
def test_report_redacts_credentials(monkeypatch):
    monkeypatch.setenv("DM_PERSIST", "1")
    monkeypatch.setenv("DM_POSTGRES_DSN", "postgresql://user:secret@db.example/dm")
    conn = _FakeConn()
    report = apply_migrations(Settings(), connect=lambda dsn: conn)
    assert "secret" not in report["dsn"]
    assert "***" in report["dsn"]


# ----------------------------------------------------------------- report render
def test_format_report_dryrun_mentions_no_connection():
    report = apply_migrations(Settings(), dry_run=True)
    text = format_report(report)
    assert "dry-run" in text
    assert "no database connection" in text
    assert MIGRATIONS[0].version in text


# --------------------------------------------------------------------- CLI module
def test_cli_dry_run_subprocess():
    """`python -m disastermind.migrations --dry-run` runs offline, exit 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "disastermind.migrations", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "dry-run" in proc.stdout
    assert "would apply" in proc.stdout


# ------------------------------------------------------- live driver path (guarded)
def test_psycopg_connect_path_importorskip(monkeypatch):
    """Exercise the real `_connect` via psycopg, skipped when the driver is absent.

    We never reach a live server (a bogus DSN with no network) — the point is to
    drive the lazy-import + guarded-connect code under the real driver, asserting
    it degrades to None rather than raising.
    """
    pytest.importorskip("psycopg")
    conn = migrations_mod._connect("postgresql://invalid:0/doesnotexist")
    assert conn is None  # unreachable -> graceful None, no exception
