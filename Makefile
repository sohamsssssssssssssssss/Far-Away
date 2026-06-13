# DisasterMind developer / operator Makefile (PRD Group A, Step 10).
#
# Thin, accurate wrappers over the real commands: the package is runnable as
# ``python -m disastermind {run,simulate,verify-audit}`` (see disastermind/cli.py)
# and the test-suite is plain ``python -m pytest``. Everything works with the
# standard library alone; the heavy extras (see pyproject.toml) are optional.

# Use the active interpreter; override with `make PYTHON=python3.13 test`.
PYTHON      ?= python3
PIP         ?= $(PYTHON) -m pip

# `simulate` module: A=cyclone/flood, B=earthquake, C=urban fire/collapse.
MODULE      ?= B
# Number of coordination cycles for `make run`.
MAX_CYCLES  ?= 10
# Audit log path for `make verify-audit`.
AUDIT       ?= audit.jsonl

# Container / compose settings.
IMAGE       ?= disastermind
TAG         ?= latest
# Host port to publish for `make docker-run` (maps to the container's $PORT).
PORT        ?= 8000
# Optional dependency extras baked into the image, e.g. EXTRAS="[all]".
EXTRAS      ?=
# SBOM output path (CycloneDX JSON).
SBOM        ?= sbom.json

.DEFAULT_GOAL := help

.PHONY: help install dev test lint typecheck typecheck-advisory reproduce demo review-packet \
        shadow-tick shadow-score shadow-verify run simulate verify-audit \
        compose-up compose-down docker-build docker-run deploy-check sbom clean

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the package (stdlib-only core, no heavy deps).
	$(PIP) install -e .

dev: ## Install the package with the dev extras (pytest, pytest-cov).
	$(PIP) install -e ".[dev]"

test: ## Run the full test-suite (stdlib only).
	$(PYTHON) -m pytest -q

lint: ## Compile-check every module (stdlib byte-compile; no extra tooling).
	$(PYTHON) -m compileall -q disastermind tests

# Single source of truth for the BLOCKING mypy gate: packages proven type-clean
# today. CI's typecheck job calls `make typecheck`; expand this list as more
# packages reach zero errors (see `make typecheck-advisory` for the full tree).
MYPY_GATED = \
  disastermind/core disastermind/tier1 disastermind/evacuation disastermind/alerting \
  disastermind/ml/eval/conformal.py disastermind/ml/eval/decision.py disastermind/ml/eval/metrics.py \
  disastermind/models disastermind/orchestration disastermind/federation \
  disastermind/multi_incident disastermind/observability disastermind/persistence \
  disastermind/roadnet disastermind/fieldapp disastermind/diagnostics \
  disastermind/benchmarks disastermind/logconfig disastermind/tier2/cascade \
  disastermind/tier2/resource disastermind/tier2/field disastermind/tier2/prediction \
  disastermind/tier3/ingestion disastermind/tier3/iot

typecheck: ## Run the BLOCKING mypy gate (type-clean packages; must stay green).
	$(PYTHON) -m mypy $(MYPY_GATED)

typecheck-advisory: ## Run mypy over the whole tree (advisory; reports the gradually-typed remainder).
	$(PYTHON) -m mypy disastermind/

reproduce: ## Regenerate every published validation number from raw fixtures and diff (offline).
	$(PYTHON) tools/reproduce.py

demo: ## Narrated command walkthrough of a real cyclone: `make demo STORM=fani|amphan`.
	$(PYTHON) -m disastermind.hindcast.walkthrough --storm $(or $(STORM),fani)

review-packet: ## Build the self-contained external-review packet (./review_packet/).
	$(PYTHON) tools/review_packet.py

SHADOW_JOURNAL ?= shadow/usgs_season.jsonl
shadow-tick: ## Pull the live USGS feed and journal new predictions + settle outcomes.
	$(PYTHON) -m disastermind.live.usgs_shadow --journal $(SHADOW_JOURNAL) --mode both
shadow-score: ## Print the running live-season scorecard (POD/FAR/AUC/Brier).
	$(PYTHON) -m disastermind.ml.shadow_season --journal $(SHADOW_JOURNAL) score
shadow-verify: ## Prove the live-season journal hash-chain is intact.
	$(PYTHON) -m disastermind.ml.shadow_season --journal $(SHADOW_JOURNAL) verify

run: ## Build the agent DAG and drive the coordination loop (PRD Step 10).
	$(PYTHON) -m disastermind run --max-cycles $(MAX_CYCLES)

simulate: ## Inject a synthetic scenario: `make simulate MODULE=A|B|C`.
	$(PYTHON) -m disastermind simulate $(MODULE)

verify-audit: ## Verify a decision-log hash-chain: `make verify-audit AUDIT=path`.
	$(PYTHON) -m disastermind verify-audit $(AUDIT)

compose-up: ## Bring up the backing stores (kafka, postgis, timescale, es, minio).
	docker compose up -d

compose-down: ## Tear down the backing stores (keeps named volumes).
	docker compose down

docker-build: ## Build the production container image (multi-stage, EXTRAS="[all]").
	docker build --build-arg EXTRAS="$(EXTRAS)" -t $(IMAGE):$(TAG) .

docker-run: ## Run the built image, serving the dashboard API on $$PORT (default 8000).
	docker run --rm -e PORT=$(PORT) -p $(PORT):$(PORT) $(IMAGE):$(TAG)

deploy-check: ## Pre-deploy gate: validate settings, build image, smoke-test offline.
	$(PYTHON) -c "import sys; from disastermind.ops import validate_settings; from disastermind.core.config import Settings; p=list(validate_settings(Settings())); print('settings OK' if not p else 'settings problems: %r' % (p,)); sys.exit(1 if p else 0)"
	$(MAKE) docker-build
	docker run --rm $(IMAGE):$(TAG) python -m disastermind simulate $(MODULE)

sbom: ## Generate a CycloneDX SBOM ($(SBOM)); prefers syft, else `pip` freeze fallback.
	@if command -v syft >/dev/null 2>&1; then \
	  syft "$(IMAGE):$(TAG)" -o cyclonedx-json > "$(SBOM)"; \
	elif $(PYTHON) -c "import cyclonedx_py" >/dev/null 2>&1; then \
	  $(PYTHON) -m cyclonedx_py environment -o "$(SBOM)"; \
	else \
	  echo "syft/cyclonedx-py not found — writing a pip-freeze fallback manifest to $(SBOM)"; \
	  printf '{"bomFormat":"CycloneDX","specVersion":"1.5","components":[\n' > "$(SBOM)"; \
	  $(PIP) freeze | awk -F'==' 'NF==2{printf "%s{\"type\":\"library\",\"name\":\"%s\",\"version\":\"%s\"}", (NR>1?",\n":""), $$1, $$2}' >> "$(SBOM)"; \
	  printf '\n]}\n' >> "$(SBOM)"; \
	fi
	@echo "SBOM written to $(SBOM)"

clean: ## Remove caches and local runtime artefacts.
	rm -rf .pytest_cache .mypy_cache .ruff_cache *.egg-info build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f $(AUDIT)
