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

.DEFAULT_GOAL := help

.PHONY: help install dev test lint run simulate verify-audit \
        compose-up compose-down docker-build clean

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

docker-build: ## Build the application container image.
	docker build -t $(IMAGE):$(TAG) .

clean: ## Remove caches and local runtime artefacts.
	rm -rf .pytest_cache .mypy_cache .ruff_cache *.egg-info build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f $(AUDIT)
