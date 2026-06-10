"""Tests for the operations documentation (DEPLOY.md + RUNBOOK.md).

These docs are part of the deployable product: a Railway deploy walkthrough
(DEPLOY.md) and an operations runbook (RUNBOOK.md), both at the PROJECT ROOT
``disastermind/`` (not the Session-B ``docs/`` directory). The tests guard that
the files exist, are substantive (not stubs), and mention the load-bearing
operational facts an operator must not miss — the Root Directory gotcha, the
``DM_API_KEYS`` / ``DM_FEEDS_LIVE`` / ``DM_PERSIST`` env switches, and the
``/healthz`` vs ``/readyz`` probes.

Stdlib only (HARD RULE 2): pure file I/O over the repo, no network, no optional
deps. The docs live next to ``pyproject.toml`` at the project root, which this
test locates by walking up from its own location.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _project_root() -> Path:
    """Return the project root (the dir holding ``pyproject.toml`` + the docs).

    ``tests/`` sits directly under the project root, so the parent of this file's
    directory is the root. We assert ``pyproject.toml`` is there to fail loudly if
    the layout ever moves, rather than silently testing the wrong directory.
    """
    root = Path(__file__).resolve().parent.parent
    assert (root / "pyproject.toml").is_file(), f"unexpected layout: no pyproject.toml under {root}"
    return root


DEPLOY = _project_root() / "DEPLOY.md"
RUNBOOK = _project_root() / "RUNBOOK.md"


# --------------------------------------------------------------------- existence
@pytest.mark.parametrize("path", [DEPLOY, RUNBOOK])
def test_doc_exists_and_is_a_file(path: Path) -> None:
    assert path.is_file(), f"missing ops doc: {path}"


@pytest.mark.parametrize("path", [DEPLOY, RUNBOOK])
def test_doc_is_non_trivial(path: Path) -> None:
    """A real doc, not a placeholder: meaningful length and multiple sections."""
    text = path.read_text(encoding="utf-8")
    assert len(text) > 1500, f"{path.name} is too short to be a real ops doc"
    # A genuine operations doc has structure: several headings and a fair number
    # of non-blank content lines.
    headings = [ln for ln in text.splitlines() if ln.lstrip().startswith("#")]
    assert len(headings) >= 4, f"{path.name} should have several sections (## headings)"
    content_lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(content_lines) >= 30, f"{path.name} has too few content lines"


# ------------------------------------------------------------------- DEPLOY.md
def test_deploy_covers_root_directory_gotcha() -> None:
    """The #1 Railway gotcha must be called out explicitly."""
    text = DEPLOY.read_text(encoding="utf-8")
    assert "Root Directory" in text, "DEPLOY.md must explain the Root Directory gotcha"
    # And it must name the subdirectory the Root Directory points at.
    assert "disastermind" in text


def test_deploy_documents_key_env_flags() -> None:
    """The DM_* env table must document the load-bearing switches."""
    text = DEPLOY.read_text(encoding="utf-8")
    for var in ("DM_API_KEYS", "DM_FEEDS_LIVE", "DM_PERSIST"):
        assert var in text, f"DEPLOY.md must document {var}"


def test_deploy_mentions_railway_essentials() -> None:
    """Grounded in the real deploy path: start command, domain, ?token= WS auth."""
    text = DEPLOY.read_text(encoding="utf-8")
    assert "python -m disastermind.api" in text  # the railway.json startCommand
    assert "Generate Domain" in text
    assert "?token=" in text  # the /ws query-string auth note


# ------------------------------------------------------------------- RUNBOOK.md
def test_runbook_documents_probes() -> None:
    """The runbook must distinguish the liveness vs readiness probes."""
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "/healthz" in text, "RUNBOOK.md must document the /healthz probe"
    assert "/readyz" in text, "RUNBOOK.md must document the /readyz probe"


def test_runbook_covers_failure_modes() -> None:
    """The four documented failure modes must each be discoverable."""
    text = RUNBOOK.read_text(encoding="utf-8").lower()
    # build fails -> Root Directory
    assert "root directory" in text
    # empty dashboard -> loop driver / tick
    assert "dm_api_drive_loop" in text or "dm_api_tick" in text
    # WS rejected -> ?token=
    assert "?token=" in text
    # DB unreachable -> degraded in-memory
    assert "in-memory" in text and ("degrad" in text)


def test_runbook_covers_ops_topics() -> None:
    """Scaling, /metrics, incident response, and backup/restore are all present."""
    text = RUNBOOK.read_text(encoding="utf-8")
    low = text.lower()
    assert "/metrics" in text
    assert "scal" in low  # scaling / scale
    assert "incident" in low
    assert "backup" in low and "restore" in low


# ------------------------------------------------------- cross-doc consistency
def test_runbook_references_key_env_flags() -> None:
    """The runbook leans on the same DM_* switches DEPLOY.md sets up."""
    text = RUNBOOK.read_text(encoding="utf-8")
    for var in ("DM_API_KEYS", "DM_PERSIST"):
        assert var in text, f"RUNBOOK.md should reference {var}"


@pytest.mark.parametrize("path", [DEPLOY, RUNBOOK])
def test_docs_cross_reference_each_other(path: Path) -> None:
    """Each doc points the reader at the other for the complementary half."""
    text = path.read_text(encoding="utf-8")
    other = "RUNBOOK" if path is DEPLOY else "DEPLOY"
    assert other in text, f"{path.name} should cross-reference {other}.md"
