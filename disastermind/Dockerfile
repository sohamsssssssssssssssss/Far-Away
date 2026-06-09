# DisasterMind container image (PRD Group A, Step 10 — productionisation).
#
# The runtime core is standard-library only (graceful degradation), so the base
# image is the slim CPython 3.13 distribution and ``pip install .`` pulls in no
# heavy dependency by default. Optional capabilities (ML, optimise, geo, bus,
# storage, feeds, dispatch) live behind the extras declared in pyproject.toml and
# can be layered in with a build-arg, e.g.:
#
#     docker build --build-arg EXTRAS="[all]" -t disastermind:all .
#
# Default entrypoint drives the coordination loop (PRD Step 10):
#     docker run --rm disastermind            # -> python -m disastermind run
#
FROM python:3.13-slim AS base

# Deterministic, unbuffered, no .pyc clutter — friendlier logs in k8s/CI.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Optional dependency extras to install with the package. Empty by default so the
# image stays slim and stdlib-only (matches the offline-by-default design).
# Override with e.g. --build-arg EXTRAS="[storage]" or "[all]".
ARG EXTRAS=""

# Copy the project metadata + sources, then install the package itself. We copy
# the whole project (respecting .dockerignore) so ``pip install .`` resolves the
# setuptools package discovery in pyproject.toml.
COPY pyproject.toml README.md ./
COPY disastermind ./disastermind

# Install the package + the web-server deps (the default CMD serves the API via
# uvicorn). They are kept out of the core runtime so the loop/CLI stay stdlib-only.
RUN pip install ".${EXTRAS}" "fastapi>=0.110" "uvicorn[standard]>=0.29"

# Non-root runtime user (least privilege for the dashboard / loop process).
RUN useradd --create-home --uid 10001 disastermind \
    && chown -R disastermind:disastermind /app
USER disastermind

# Serve the Commander Dashboard API by default — binds $PORT (Railway/Heroku/Fly)
# on 0.0.0.0, else 127.0.0.1:8000. Override to drive the loop or a scenario, e.g.:
#   docker run disastermind python -m disastermind run
#   docker run disastermind python -m disastermind simulate B
CMD ["python", "-m", "disastermind.api"]
