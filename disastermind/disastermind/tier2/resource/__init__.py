"""Tier 2 resource-optimisation module (PRD Step 4).

Re-exports the module factory (:func:`build_agents`) so the orchestration layer
can build the resource DAG node uniformly.
"""
from .build import build_agents

__all__ = ["build_agents"]
