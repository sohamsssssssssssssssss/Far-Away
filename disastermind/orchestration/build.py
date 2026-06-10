"""Orchestration entry point.

Re-exports the bootstrap so callers do:

    from disastermind.orchestration.build import build_system
    loop = build_system()
    loop.run(max_cycles=10)            # or loop.run_once(now) for stepping
"""
from __future__ import annotations

from .loop import MODULE_BUILD_PATHS, CoordinationLoop, build_system
from .triggers import (
    ActivationDecision,
    Signals,
    activation_report,
    should_activate,
)

__all__ = [
    "build_system",
    "CoordinationLoop",
    "MODULE_BUILD_PATHS",
    "should_activate",
    "activation_report",
    "Signals",
    "ActivationDecision",
]
