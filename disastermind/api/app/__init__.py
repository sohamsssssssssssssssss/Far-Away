"""Thin FastAPI transport for the Commander Dashboard (PRD Step 7 + Step 10).

This is the *transport* half of the dashboard; all policy lives in the
framework-free :class:`~disastermind.api.service.DashboardService`. FastAPI is an
optional, heavy dependency, so it is imported **lazily inside**
:func:`create_app` (HARD RULE 2): importing this module never requires FastAPI
and never touches the network. Environments without FastAPI still get the full
:class:`DashboardService` for programmatic / test use.

Endpoints (PRD Step 7 + production hardening):
  * ``GET  /health``                       — liveness snapshot (back-compat)
  * ``GET  /healthz``                       — process liveness (always 200 if up)
  * ``GET  /readyz``                        — readiness (200 only when wired)
  * ``GET  /metrics``                       — Prometheus text exposition
  * ``GET  /topics``                       — per-topic message counts
  * ``GET  /incidents``                    — recent incident roll-up (``?limit=&offset=``)
  * ``GET  /recent``                       — recent bus messages (``?limit=&offset=``)
  * ``GET  /escalations``                  — open escalations (``?limit=&offset=``)
  * ``POST /escalations/{id}/approve``     — human approves -> dispatch
  * ``POST /escalations/{id}/reject``      — human rejects  -> rejection ACK
  * ``WS   /ws``                           — live stream of new bus messages (Step 10)

Production middleware (opt-in, inert without FastAPI):
  * structured per-request logging with an ``X-Request-ID`` (generated if absent,
    echoed back), and a consistent JSON error envelope via exception handlers;
  * security headers (``X-Content-Type-Options``, ``X-Frame-Options``,
    ``Referrer-Policy``, and ``Strict-Transport-Security`` behind TLS).

Pagination is **backward compatible**: ``/incidents``, ``/recent`` and
``/escalations`` return a bare JSON array by default (no ``limit``/``offset``
query params), exactly as before. When a caller supplies ``?limit=`` and/or
``?offset=`` the response becomes the paginated envelope
``{"items": [...], "total": N, "limit": L, "offset": O}``. This keeps the
existing clients/tests green while giving new clients real pagination.

Layout (this module was split from a single ``app.py`` for readability; the
public surface — :func:`create_app` and :func:`build_service` — is unchanged
and still importable as ``from disastermind.api.app import ...``):
  * :mod:`._constants`   — tuning constants (page limit, ``/v1`` prefix, body/WS caps)
  * :mod:`._env`         — defensive ``DM_*`` env-var readers
  * :mod:`._errors`      — JSON-decode classification for exception handlers
  * :mod:`._persistence` — locate the durable store behind a built loop
  * :mod:`.pagination`   — the back-compat list/envelope helper
  * :mod:`.service`      — :func:`build_service` system wiring
  * :mod:`.factory`      — :func:`create_app`, the FastAPI route assembler
"""
from ._constants import (
    _API_V1,
    _DEFAULT_MAX_BODY,
    _DEFAULT_PAGE_LIMIT,
    _DEFAULT_WS_MAX,
    _DEFAULT_WS_PING,
)
from ._env import _env_float, _env_int
from ._errors import _is_json_decode
from ._persistence import _find_persisted_storage
from .factory import create_app
from .pagination import _paginate
from .service import build_service, log

__all__ = ["create_app", "build_service"]
