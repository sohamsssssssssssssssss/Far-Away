"""Orchestration: triggers (PRD Step 1) + the 30s coordination loop (PRD Step 10)."""
from .build import (  # noqa: F401
    ActivationDecision,
    CoordinationLoop,
    Signals,
    activation_report,
    build_system,
    should_activate,
)
