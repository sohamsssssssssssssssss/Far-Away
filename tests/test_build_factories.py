"""Every module package must expose the uniform ``build_agents`` factory.

PRD Group A wiring contract: the orchestrator builds each DAG node by importing
``<module>.build`` and calling ``build_agents(bus, logger, settings)``. This was
the load-bearing break — ingestion / cascade / resource had no build.py at all,
so the orchestrator could not instantiate those nodes (ModuleNotFoundError).
"""
from __future__ import annotations

import importlib

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.agent import BaseAgent
from disastermind.core.bus import InMemoryBus
from disastermind.core.config import Settings

ALL_MODULE_BUILD_PATHS = [
    "disastermind.tier3.ingestion.build",
    "disastermind.tier3.iot.build",
    "disastermind.tier2.prediction.build",
    "disastermind.tier2.cascade.build",
    "disastermind.tier2.resource.build",
    "disastermind.tier2.routing.build",
    "disastermind.tier2.field.build",
    "disastermind.tier1.commander.build",
    "disastermind.tier3.dispatch.build",
]

# The three modules that were missing a factory entirely.
PREVIOUSLY_MISSING = [
    "disastermind.tier3.ingestion.build",
    "disastermind.tier2.cascade.build",
    "disastermind.tier2.resource.build",
]


@pytest.mark.parametrize("path", ALL_MODULE_BUILD_PATHS)
def test_build_module_importable_and_has_factory(path: str) -> None:
    mod = importlib.import_module(path)
    assert hasattr(mod, "build_agents"), f"{path} missing build_agents"
    assert callable(mod.build_agents)


@pytest.mark.parametrize("path", ALL_MODULE_BUILD_PATHS)
def test_build_returns_base_agents(path: str) -> None:
    mod = importlib.import_module(path)
    bus = InMemoryBus()
    logger = DecisionLogger.null()
    settings = Settings()
    agents = mod.build_agents(bus, logger, settings)
    assert isinstance(agents, list)
    assert agents, f"{path}.build_agents returned no agents"
    for a in agents:
        assert isinstance(a, BaseAgent)


@pytest.mark.parametrize("path", PREVIOUSLY_MISSING)
def test_previously_missing_modules_now_build(path: str) -> None:
    """Regression guard for the original ModuleNotFoundError failures."""
    mod = importlib.import_module(path)
    agents = mod.build_agents(InMemoryBus(), DecisionLogger.null(), Settings())
    assert agents


def test_module_package_reexports_factory() -> None:
    """The package ``__init__`` re-exports the factory (alternate import path)."""
    for pkg in (
        "disastermind.tier3.ingestion",
        "disastermind.tier2.cascade",
        "disastermind.tier2.resource",
    ):
        mod = importlib.import_module(pkg)
        assert hasattr(mod, "build_agents"), f"{pkg} does not re-export build_agents"
