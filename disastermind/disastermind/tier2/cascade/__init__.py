"""Tier 2 cascade-prediction module (PRD Step 3).

Re-exports the module factory (:func:`build_agents`) so the orchestration layer
can build the cascade DAG node uniformly.
"""
from .build import build_agents

__all__ = ["build_agents"]
