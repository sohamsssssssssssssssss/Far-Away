"""Dashboard hardening for DisasterMind (PRD Step 7).

The Commander Dashboard is the one human-facing surface of an otherwise
autonomous, multi-agent system, so it is the surface that needs hardening. This
package adds three stdlib-only, opt-in defences that the ``api`` server *can*
adopt without this package ever editing ``api/*`` (HARD RULE 1):

* :mod:`~disastermind.security.auth` — API-key / bearer-token verification with a
  :class:`TokenStore`, :func:`authenticate`, a framework-agnostic
  :func:`require_auth` factory, and a lazy (optional) FastAPI dependency. **Off by
  default**: enforced only when keys are configured, so existing routes/tests are
  unaffected.
* :mod:`~disastermind.security.ratelimit` — an in-memory token-bucket
  :class:`RateLimiter`, one bucket per principal.
* :mod:`~disastermind.security.validation` — :func:`validate_message_payload`,
  structural payload checks per the well-known topic conventions.

Everything is importable with the standard library only; optional libraries
(FastAPI) are imported lazily with a deterministic fallback (HARD RULE 2).
"""
from __future__ import annotations

from .auth import (
    AUTHORIZATION_HEADER,
    BEARER_PREFIX,
    DEFAULT_ROLE,
    ENV_API_KEYS,
    ENV_API_KEYS_MAP,
    ENV_API_SCOPES,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SCOPES,
    ROLE_VIEWER,
    AuthError,
    AuthGuard,
    Principal,
    TokenStore,
    authenticate,
    extract_bearer,
    fastapi_auth_dependency,
    require_auth,
    role_to_scopes,
)
from .ratelimit import (
    DEFAULT_CAPACITY,
    DEFAULT_IP_CAPACITY,
    DEFAULT_IP_REFILL_PER_SECOND,
    DEFAULT_REFILL_PER_SECOND,
    ENV_RATE_CAPACITY,
    ENV_RATE_IP_CAPACITY,
    ENV_RATE_IP_REFILL,
    ENV_RATE_REFILL,
    RateLimiter,
    RateLimitResult,
    client_ip,
    ip_rate_limiter,
)
from .validation import known_topics, validate_message_payload

__all__ = [
    # auth
    "AUTHORIZATION_HEADER",
    "BEARER_PREFIX",
    "DEFAULT_ROLE",
    "ENV_API_KEYS",
    "ENV_API_KEYS_MAP",
    "ENV_API_SCOPES",
    "ROLE_ADMIN",
    "ROLE_OPERATOR",
    "ROLE_SCOPES",
    "ROLE_VIEWER",
    "AuthError",
    "AuthGuard",
    "Principal",
    "TokenStore",
    "authenticate",
    "extract_bearer",
    "fastapi_auth_dependency",
    "require_auth",
    "role_to_scopes",
    # ratelimit
    "DEFAULT_CAPACITY",
    "DEFAULT_IP_CAPACITY",
    "DEFAULT_IP_REFILL_PER_SECOND",
    "DEFAULT_REFILL_PER_SECOND",
    "ENV_RATE_CAPACITY",
    "ENV_RATE_IP_CAPACITY",
    "ENV_RATE_IP_REFILL",
    "ENV_RATE_REFILL",
    "RateLimiter",
    "RateLimitResult",
    "client_ip",
    "ip_rate_limiter",
    # validation
    "known_topics",
    "validate_message_payload",
]
