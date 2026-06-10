"""Production deploy-hardening artefact tests (PRD Group A, Step 10).

Validates the *hardened* deployment surface added on top of the basic packaging
checks in ``test_packaging.py``:

  * the ``Dockerfile`` is a genuine **multi-stage** build (>= 2 ``FROM`` stages),
    declares a ``HEALTHCHECK`` against the API health endpoint, pins the python
    base by a specific tag, keeps a non-root user, and still serves the dashboard
    API as the default ``CMD``;
  * a CI workflow exists at the **git-repository root** (``.github/workflows/
    ci.yml``) — GitHub only runs workflows from the repo root, not the nested
    ``disastermind/`` project dir — that ``cd``s into the project, installs the
    dev extras editable, and runs ``pytest`` across the 3.11/3.12/3.13 matrix;
  * the ``Makefile`` carries the production targets (``docker-build``,
    ``docker-run``, ``deploy-check``, ``sbom``).

Stdlib only: artefacts are parsed as **text** for the substring assertions, and
YAML *structure* assertions are guarded with ``pytest.importorskip("yaml")`` so
the suite stays green under the standard library alone (HARD RULE 2): no PyYAML
required, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# This test file lives at  <gitroot>/tests/test_deploy_hardening.py
# The project lives at the repository root: PROJECT == GIT_ROOT (Dockerfile,
# Makefile, SECURITY.md, pyproject, the disastermind/ package and .github/ all
# live here).
PROJECT = Path(__file__).resolve().parent.parent
GIT_ROOT = PROJECT

DOCKERFILE = PROJECT / "Dockerfile"
MAKEFILE = PROJECT / "Makefile"
SECURITY = PROJECT / "SECURITY.md"
ROOT_CI = GIT_ROOT / ".github" / "workflows" / "ci.yml"


# ---------------------------------------------------------------- helpers (text)
def _read(path: Path) -> str:
    assert path.is_file(), f"missing artefact: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"empty artefact: {path}"
    return text


# ------------------------------------------------------------------- Dockerfile
def test_dockerfile_is_multi_stage() -> None:
    text = _read(DOCKERFILE)
    # A multi-stage build has at least two `FROM` instructions; we additionally
    # require named stages (`FROM ... AS <name>`), which is what makes the slim
    # final image able to copy from the builder.
    from_lines = re.findall(r"(?im)^\s*FROM\s+\S+", text)
    assert len(from_lines) >= 2, f"Dockerfile is not multi-stage (FROMs={len(from_lines)})"
    as_stages = re.findall(r"(?im)^\s*FROM\s+\S+\s+AS\s+\S+", text)
    assert len(as_stages) >= 2, "expected >= 2 named build stages (FROM ... AS ...)"
    # The final stage copies from an earlier stage — the essence of multi-stage.
    assert re.search(r"(?im)^\s*COPY\s+--from=", text), "final stage must COPY --from a builder"


def test_dockerfile_pins_python_313_base() -> None:
    text = _read(DOCKERFILE)
    # Keep the python:3.13 base + a specific (non-floating) tag suffix.
    assert "python:3.13-slim" in text, "must keep the python:3.13-slim base"
    # A pinned tag has something after `3.13-slim` (e.g. -bookworm), not a bare/`latest`.
    assert re.search(r"python:3\.13-slim-\S+", text), "python base should be pinned to a specific tag"


def test_dockerfile_has_healthcheck_on_health_endpoint() -> None:
    text = _read(DOCKERFILE)
    assert re.search(r"(?im)^\s*HEALTHCHECK\b", text), "Dockerfile missing HEALTHCHECK"
    # The probe targets the API health endpoint (served /health, or /healthz alias).
    assert ("/healthz" in text) or ("/health" in text), "HEALTHCHECK must hit the health endpoint"


def test_dockerfile_serves_api_cmd_and_keeps_pip_install() -> None:
    text = _read(DOCKERFILE)
    # Default CMD still serves the dashboard API (binds $PORT for hosted platforms).
    assert '"python", "-m", "disastermind.api"' in text, "default CMD must serve the API"
    # Keep an actual `pip install ".` so test_packaging stays green.
    assert 'pip install ".' in text, "must keep `pip install \".${EXTRAS}\"`"


def test_dockerfile_keeps_non_root_user() -> None:
    text = _read(DOCKERFILE)
    assert re.search(r"(?im)^\s*USER\s+disastermind\b", text), "runtime must drop to a non-root user"


# --------------------------------------------------------------- git-root CI yml
def test_root_ci_workflow_exists_and_nonempty() -> None:
    _read(ROOT_CI)  # asserts existence + non-empty


def test_root_ci_references_pytest_and_matrix() -> None:
    text = _read(ROOT_CI)
    # The 3.x interpreter matrix.
    for ver in ("3.11", "3.12", "3.13"):
        assert ver in text, f"git-root CI matrix missing Python {ver}"
    # Installs the package editable with dev extras and runs pytest.
    assert "pip install -e .[dev]" in text, "CI should install -e .[dev]"
    assert "pytest" in text, "CI must run pytest"
    # Runs with the ignores for the other workstreams' trees.
    assert "--ignore=tests/integration" in text
    assert "--ignore=clients" in text


def test_root_ci_runs_at_root_and_builds_docker() -> None:
    text = _read(ROOT_CI)
    # The project lives at the repo root, so the workflow runs there directly and
    # must NOT cd into a nested project dir.
    assert "working-directory: disastermind" not in text and "cd disastermind" not in text, (
        "CI should run at the repo root, not cd into a nested project dir"
    )
    # It still references the package (e.g. `mypy disastermind/`) and builds the image.
    assert "disastermind" in text
    assert ("docker build" in text) or ("docker/build-push-action" in text), "CI needs a docker build job"


def test_root_ci_parses_as_yaml() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_read(ROOT_CI))
    assert isinstance(doc, dict)
    assert "jobs" in doc and isinstance(doc["jobs"], dict) and doc["jobs"], "CI needs jobs"


# ----------------------------------------------------------------- Makefile prod
def test_makefile_has_prod_targets() -> None:
    text = _read(MAKEFILE)
    for target in ("docker-build:", "docker-run:", "deploy-check:", "sbom:"):
        assert target in text, f"Makefile missing prod target: {target}"
    # The targets are wired to the real container commands.
    assert "docker build" in text
    assert "docker run" in text


def test_makefile_targets_are_phony() -> None:
    text = _read(MAKEFILE)
    phony = "".join(re.findall(r"(?ms)^\.PHONY:.*?(?=^\S|\Z)", text))
    for target in ("docker-run", "deploy-check", "sbom"):
        assert target in phony, f".PHONY should list {target}"


# ------------------------------------------------------------------- SECURITY.md
def test_security_policy_covers_threat_model() -> None:
    text = _read(SECURITY).lower()
    # A real policy: reporting + the controls/gaps the task calls out.
    assert "vulnerability" in text, "SECURITY.md needs a vulnerability-reporting section"
    for topic in ("auth", "cors", "rate", "?token=", "threat model"):
        assert topic in text, f"SECURITY.md threat model missing: {topic!r}"
    # Honest 'what's NOT yet done' section.
    assert "not yet" in text or "gaps" in text or "tls" in text
