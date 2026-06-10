"""Tier 3 feed-ingestion module (PRD Step 2).

Re-exports the module factory (:func:`build_agents`) so the orchestration layer
can build the ingestion DAG node uniformly.
"""
from .build import build_agents

__all__ = ["build_agents"]
