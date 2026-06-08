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

RUN pip install ".${EXTRAS}"

# Non-root runtime user (least privilege for the dashboard / loop process).
RUN useradd --create-home --uid 10001 disastermind \
    && chown -R disastermind:disastermind /app
USER disastermind

# Drive the live coordination loop by default (PRD Step 10). Override the CMD to
# serve the dashboard API or run a scenario, e.g.:
#   docker run disastermind simulate B
#   docker run -p 8000:8000 disastermind \
#       sh -c "uvicorn disastermind.api:create_app --factory --host 0.0.0.0 --port 8000"
CMD ["python", "-m", "disastermind", "run"]
