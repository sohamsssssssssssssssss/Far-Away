"""Tier 2 — Field Coordination module (PRD Step 6).

Real-time tracking of NDRF/SDRF teams, boats and helicopters; fuses resource
and routing plans into per-team field orders, autonomously reassigns teams and
requests extra resources, and hints escalations for the Tier 1 commander.
"""
from __future__ import annotations

from .agent import FieldCoordinationAgent
from .build import build_agents

__all__ = ["FieldCoordinationAgent", "build_agents"]
