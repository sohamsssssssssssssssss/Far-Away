"""DisasterMind Commander Dashboard backend (PRD Step 7 + Step 10).

Public surface:
  * :class:`~disastermind.api.service.DashboardService` — framework-free policy
    layer (stdlib only): topic counts, recent feed, incident roll-up, and
    escalation approve/reject delegated to the live Tier-1 Commander.
  * :func:`~disastermind.api.app.create_app` — thin FastAPI transport (FastAPI
    imported lazily; importing this package never requires it).
  * :func:`~disastermind.api.app.build_service` — wire a service over a full
    DisasterMind system.

Importing this package is stdlib-only and never touches the network.
"""
from __future__ import annotations

from .app import build_service, create_app
from .service import WS_STREAM, DashboardService

__all__ = ["DashboardService", "create_app", "build_service", "WS_STREAM"]
