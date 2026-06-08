"""Packaging / deployment artefact tests (PRD Group A, Step 10 productionisation).

Validates the deployment surface owned by the ``deploy`` package:
  * the container image build (``Dockerfile`` + ``.dockerignore``),
  * the developer/operator ``Makefile``,
  * the GitHub Actions CI workflow (``.github/workflows/ci.yml``),
  * the Kubernetes manifests under ``deploy/k8s/`` (dashboard Deployment+Service,
    a ConfigMap built from the ``DM_*`` keys, and StatefulSets+Services mirroring
    the docker-compose backing stores).

Existence checks are pure stdlib. YAML *parsing* assertions are guarded with
``pytest.importorskip("yaml")`` so the suite stays green under the standard
library alone (PRD HARD RULE 2 / HARD RULE 4): no PyYAML, no network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Repo root: tests/ lives directly under it.
ROOT = Path(__file__).resolve().parent.parent

# Every k8s manifest we ship; each must exist and parse as YAML.
K8S_DIR = ROOT / "deploy" / "k8s"
K8S_MANIFESTS = [
    "configmap.yaml",
    "dashboard.yaml",
    "kafka.yaml",
    "postgis.yaml",
    "timescaledb.yaml",
    "elasticsearch.yaml",
    "minio.yaml",
]

# Top-level deployment artefacts that must exist.
TOP_LEVEL_FILES = [
    "Dockerfile",
    ".dockerignore",
    "Makefile",
    ".github/workflows/ci.yml",
]


# --------------------------------------------------------------- existence (stdlib)
@pytest.mark.parametrize("rel", TOP_LEVEL_FILES)
def test_top_level_artefact_exists(rel: str) -> None:
    path = ROOT / rel
    assert path.is_file(), f"missing deployment artefact: {rel}"
    assert path.read_text(encoding="utf-8").strip(), f"empty deployment artefact: {rel}"


@pytest.mark.parametrize("name", K8S_MANIFESTS)
def test_k8s_manifest_exists(name: str) -> None:
    path = K8S_DIR / name
    assert path.is_file(), f"missing k8s manifest: deploy/k8s/{name}"
    assert path.read_text(encoding="utf-8").strip(), f"empty k8s manifest: {name}"


# ------------------------------------------------------ Dockerfile / Makefile / CI
def test_dockerfile_uses_python_313_and_pip_install() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.13-slim" in text
    assert 'pip install ".' in text  # `pip install ".${EXTRAS}"`
    assert '"python", "-m", "disastermind", "run"' in text


def test_makefile_has_required_targets() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "install:",
        "test:",
        "lint:",
        "run:",
        "simulate:",
        "compose-up:",
        "docker-build:",
    ):
        assert target in text, f"Makefile missing target: {target}"
    # Targets use the real commands.
    assert "python -m pytest" in text
    assert "python -m disastermind" in text
    assert "docker compose up" in text
    assert "docker build" in text


def test_ci_workflow_matrix_and_pytest() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for ver in ("3.11", "3.12", "3.13"):
        assert ver in text, f"CI matrix missing Python {ver}"
    assert "pip install -e .[dev]" in text
    assert "python -m pytest -q" in text


# --------------------------------------------------------- YAML parsing (guarded)
def test_ci_workflow_parses_as_yaml() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    assert "jobs" in doc and isinstance(doc["jobs"], dict)


@pytest.mark.parametrize("name", K8S_MANIFESTS)
def test_k8s_manifest_parses_as_yaml(name: str) -> None:
    yaml = pytest.importorskip("yaml")
    text = (K8S_DIR / name).read_text(encoding="utf-8")
    docs = [d for d in yaml.safe_load_all(text) if d is not None]
    assert docs, f"no YAML documents in {name}"
    for doc in docs:
        assert isinstance(doc, dict), f"non-mapping document in {name}"
        assert "apiVersion" in doc and "kind" in doc, f"not a k8s object in {name}"
        assert doc.get("metadata", {}).get("name"), f"unnamed object in {name}"


def test_configmap_mirrors_dm_env_keys() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((K8S_DIR / "configmap.yaml").read_text(encoding="utf-8"))
    assert doc["kind"] == "ConfigMap"
    data = doc["data"]
    # A representative slice of the DM_* keys read by core/config.py Settings.
    for key in (
        "DM_LOOP_INTERVAL",
        "DM_USE_KAFKA",
        "DM_KAFKA_BROKERS",
        "DM_KAFKA_BACKUP",
        "DM_POSTGRES_DSN",
        "DM_TIMESCALE_DSN",
        "DM_ELASTICSEARCH_URL",
        "DM_AUDIT_LOG",
    ):
        assert key in data, f"ConfigMap missing {key}"
    # ConfigMap values must be strings (k8s requirement).
    assert all(isinstance(v, str) for v in data.values())


def test_dashboard_has_deployment_and_service() -> None:
    yaml = pytest.importorskip("yaml")
    text = (K8S_DIR / "dashboard.yaml").read_text(encoding="utf-8")
    kinds = {d["kind"] for d in yaml.safe_load_all(text) if d}
    assert {"Deployment", "Service"} <= kinds


def test_backing_stores_are_statefulsets() -> None:
    yaml = pytest.importorskip("yaml")
    for store in ("kafka", "postgis", "timescaledb", "elasticsearch", "minio"):
        text = (K8S_DIR / f"{store}.yaml").read_text(encoding="utf-8")
        kinds = {d["kind"] for d in yaml.safe_load_all(text) if d}
        assert "StatefulSet" in kinds, f"{store} should ship a StatefulSet"
        assert "Service" in kinds, f"{store} should ship a Service"
