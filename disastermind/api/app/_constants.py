"""Module-level tuning constants for the dashboard transport.

These are split out of the (previously single-file) ``app`` module so the
env helpers, service factory and route factory can share one source of truth
without importing each other. Values are unchanged from the original
``app.py``; only their home moved.
"""

# A "default large" limit so that an unpaginated list view still returns the full
# recent window rather than truncating. Callers that pass ``?limit=`` override it.
_DEFAULT_PAGE_LIMIT = 1000

# Versioned mount prefix. Every data route is registered both unversioned (legacy
# back-compat alias) AND under this prefix (``/v1/...``) so new clients can pin a
# version while existing clients/tests keep working unchanged.
_API_V1 = "/v1"

# Generous default request-body ceiling (bytes). The dashboard's POSTs are tiny
# (approve/reject carry only query params), so this guards against accidental or
# hostile oversize bodies without ever clipping a legitimate request. Overridable
# via ``DM_MAX_BODY``.
_DEFAULT_MAX_BODY = 1 * 1024 * 1024  # 1 MiB

# WebSocket hardening defaults (overridable via env). A server-side heartbeat ping
# every ``DM_WS_PING`` seconds prunes dead/half-open clients; ``DM_WS_MAX`` caps
# concurrent live connections so a connection flood cannot exhaust the box.
_DEFAULT_WS_PING = 20.0
_DEFAULT_WS_MAX = 256
