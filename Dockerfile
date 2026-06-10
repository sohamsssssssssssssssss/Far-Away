# DisasterMind container image (PRD Group A, Step 10 — productionisation).
#
# MULTI-STAGE build:
#   * ``builder`` — pins a full CPython toolchain, creates an isolated virtualenv
#     and ``pip install``s the package (+ web-server deps) into it.
#   * ``runtime``  — a slim CPython image that copies ONLY the prebuilt venv and
#     the package source. No build toolchain, no pip cache, no test/dev cruft.
#
# The runtime core is standard-library only (graceful degradation), so the base
# image is the slim CPython 3.13 distribution and ``pip install .`` pulls in no
# heavy dependency by default. Optional capabilities (ML, optimise, geo, bus,
# storage, feeds, dispatch) live behind the extras declared in pyproject.toml and
# can be layered in with a build-arg, e.g.:
#
#     docker build --build-arg EXTRAS="[all]" -t disastermind:all .
#
# Default entrypoint serves the Commander Dashboard API (PRD Step 7 + 10).

# ----------------------------------------------------------------- stage: builder
# Base pinned to a specific, immutable tag (Bookworm point release) so rebuilds
# are reproducible and CVE scanning is deterministic. Keeps the ``python:3.13-slim``
# substring required by tests/test_packaging.py.
FROM python:3.13-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VENV=/opt/venv

WORKDIR /build

# Create an isolated virtualenv; everything installs into it so the runtime stage
# can copy one self-contained tree (no system site-packages, no build tooling).
RUN python -m venv "$VENV"
ENV PATH="/opt/venv/bin:$PATH"

# Optional dependency extras to install with the package. Empty by default so the
# image stays slim and stdlib-only (matches the offline-by-default design).
# Override with e.g. --build-arg EXTRAS="[storage]" or "[all]".
ARG EXTRAS=""

# Copy the project metadata + sources, then install the package into the venv. We
# copy the whole project (respecting .dockerignore) so ``pip install .`` resolves
# the setuptools package discovery in pyproject.toml.
COPY pyproject.toml README.md ./
COPY disastermind ./disastermind

# Install the package + the web-server deps (the default CMD serves the API via
# uvicorn). They are kept out of the core runtime so the loop/CLI stay stdlib-only.
RUN pip install ".${EXTRAS}" "fastapi>=0.110" "uvicorn[standard]>=0.29"

# ----------------------------------------------------------------- stage: runtime
FROM python:3.13-slim-bookworm AS runtime

# Deterministic, unbuffered, no .pyc clutter — friendlier logs in k8s/CI. Put the
# copied venv first on PATH so ``python`` resolves to the installed interpreter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Non-root runtime user (least privilege for the dashboard / loop process).
RUN useradd --create-home --uid 10001 disastermind

# Copy ONLY the prebuilt virtualenv and the package source from the builder; no
# build toolchain, no pip cache, no project metadata leaks into the final image.
COPY --from=builder --chown=disastermind:disastermind /opt/venv /opt/venv
COPY --chown=disastermind:disastermind disastermind ./disastermind

USER disastermind

# The dashboard binds $PORT (Railway/Heroku/Fly) on 0.0.0.0, else 127.0.0.1:8000.
ENV PORT=8000
EXPOSE 8000

# Liveness probe — hits the API health endpoint with the stdlib (slim images have
# no curl/wget). Uses $PORT so it tracks the hosted bind port, and tolerates
# either ``/healthz`` (ops/observability naming) or the served ``/health`` route.
# A non-2xx or unreachable endpoint exits non-zero -> Docker marks it unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request as u\nport=os.environ.get('PORT','8000')\nfor path in ('/healthz','/health'):\n    try:\n        with u.urlopen('http://127.0.0.1:%s%s' % (port, path), timeout=4) as r:\n            if r.status < 400: sys.exit(0)\n    except Exception: pass\nsys.exit(1)"]

# Serve the Commander Dashboard API by default — binds $PORT (Railway/Heroku/Fly)
# on 0.0.0.0, else 127.0.0.1:8000. Override to drive the loop or a scenario, e.g.:
#   docker run disastermind python -m disastermind run
#   docker run disastermind python -m disastermind simulate B
CMD ["python", "-m", "disastermind.api"]
