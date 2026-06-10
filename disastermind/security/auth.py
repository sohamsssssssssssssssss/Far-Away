"""API authentication for the human-facing Commander Dashboard (PRD Step 7).

The dashboard (``disastermind.api``) is the one human-facing surface of an
otherwise autonomous system, so it is the part that needs hardening. This module
adds **opt-in** API-key / bearer-token verification that is:

* **framework-agnostic** — the load-bearing logic lives in :class:`TokenStore`
  and :func:`authenticate`; :func:`require_auth` wraps any :class:`Dashboard
  service`-style object without a web framework, and an *optional* lazy FastAPI
  dependency (:func:`fastapi_auth_dependency`) is provided for callers that mount
  a real app.
* **off by default** — auth is enforced **only when keys are configured** (via
  :class:`~disastermind.core.config.Settings` env vars or an explicit store). An
  unconfigured store is *open*, so the existing ``api/*`` routes and tests are
  completely unaffected (HARD RULE 1 — we never edit ``api/*``; this is
  mountable middleware the server *could* adopt).

No network and no heavy imports happen at import time (HARD RULE 2): FastAPI is
imported lazily inside :func:`fastapi_auth_dependency` and degrades to a plain
``RuntimeError`` when absent.

Token sources, in precedence order, all from the environment so secrets never
live in code:

* ``DM_API_KEYS`` — comma/whitespace-separated bearer tokens (the simple form).
* ``DM_API_KEYS_MAP`` — ``principal:token`` pairs (comma-separated) when you want
  named principals (e.g. ``commander:abc123,observer:def456``). An OPTIONAL third
  field assigns an RBAC role: ``alice:tok1:admin,bob:tok2:operator`` (see below).
* ``DM_API_SCOPES`` — an *additional* ``principal:role`` (or ``principal:scope``)
  map layered onto already-registered principals, e.g. ``alice:admin,bob:viewer``.

A bare token from ``DM_API_KEYS`` is given the principal name equal to a short,
non-reversible fingerprint so audit logs can attribute actions without leaking
the secret. Its role defaults to ``operator`` so existing single-key deployments
keep their read+act access unchanged (back-compat).

RBAC roles (PRD Step 7 access control) are hierarchical and expand to scopes:

* ``viewer``   → ``{viewer}``                     — read-only (GET routes)
* ``operator`` → ``{viewer, operator}``           — read + approve/reject
* ``admin``    → ``{viewer, operator, admin}``     — everything, incl. admin routes

RBAC is enforced by the transport layer **only when scoped/role tokens are
configured** (``DM_API_KEYS_MAP`` with roles, or ``DM_API_SCOPES``); a store of
plain ``DM_API_KEYS`` tokens stays role-flat (everyone ``operator``) so existing
auth tests and deployments are unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from typing import Any, Callable

# Topic/constant strings are owned by THIS package (HARD RULE 3 — never edit
# core/contracts.py). These are the env vars the TokenStore reads.
ENV_API_KEYS = "DM_API_KEYS"
ENV_API_KEYS_MAP = "DM_API_KEYS_MAP"
ENV_API_SCOPES = "DM_API_SCOPES"

#: HTTP header carrying the bearer token (case-insensitive in practice).
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "

# ---------------------------------------------------------------------- RBAC roles
#: Canonical role names, weakest -> strongest. A token carries exactly one role;
#: the role expands to a (hierarchical) scope set via :data:`ROLE_SCOPES`.
ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"

#: A bare ``DM_API_KEYS`` token keeps read+act access (back-compat) by defaulting
#: to ``operator`` — never ``viewer`` — so existing single-key deploys are unchanged.
DEFAULT_ROLE = ROLE_OPERATOR

#: Hierarchical role -> scope expansion. Each stronger role is a strict superset
#: of the weaker ones, so ``has_scope('viewer')`` is true for operator/admin etc.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    ROLE_VIEWER: frozenset({ROLE_VIEWER}),
    ROLE_OPERATOR: frozenset({ROLE_VIEWER, ROLE_OPERATOR}),
    ROLE_ADMIN: frozenset({ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN}),
}


def role_to_scopes(role: str | None) -> frozenset[str]:
    """Expand an RBAC ``role`` name to its hierarchical scope set.

    Unknown / empty roles map to the lone scope equal to the role string (so a
    custom ``DM_API_SCOPES`` value like ``ops:billing`` still yields a usable
    ``{billing}`` scope), defaulting to :data:`DEFAULT_ROLE` when blank.
    """
    if not role:
        role = DEFAULT_ROLE
    role = role.strip().lower()
    scopes = ROLE_SCOPES.get(role)
    if scopes is not None:
        return scopes
    return frozenset({role})


@dataclass(frozen=True)
class Principal:
    """An authenticated caller.

    Attributes
    ----------
    name:
        Stable identifier used for audit attribution (PRD Step 9) and as the
        rate-limiter bucket key (see :mod:`disastermind.security.ratelimit`).
    fingerprint:
        A short, non-reversible digest of the presented token — safe to log.
    scopes:
        Optional capability tags. Empty means "default access"; callers may
        require a scope via :meth:`has_scope` for finer-grained control.
    """

    name: str
    fingerprint: str
    scopes: frozenset[str] = frozenset()

    def has_scope(self, scope: str) -> bool:
        """True when this principal carries ``scope`` (or has no scope restriction)."""
        return not self.scopes or scope in self.scopes


def _fingerprint(token: str) -> str:
    """Short, non-reversible token digest, safe for logs (never the raw secret)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _parse_keys_csv(raw: str) -> list[str]:
    """Split a comma/whitespace-separated token list, dropping blanks."""
    parts: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        tok = chunk.strip()
        if tok:
            parts.append(tok)
    return parts


def _parse_keys_map(raw: str) -> dict[str, tuple[str, str | None]]:
    """Parse ``principal:token[:role]`` pairs into ``{token: (principal, role)}``.

    The third ``:role`` field is OPTIONAL (RBAC). Lines without it parse to a
    ``None`` role (the caller then applies :data:`DEFAULT_ROLE`), so the legacy
    two-field ``principal:token`` form is fully back-compatible.
    """
    mapping: dict[str, tuple[str, str | None]] = {}
    for chunk in raw.replace("\n", ",").split(","):
        item = chunk.strip()
        if not item or ":" not in item:
            continue
        parts = [p.strip() for p in item.split(":")]
        principal = parts[0]
        token = parts[1] if len(parts) > 1 else ""
        role = parts[2] if len(parts) > 2 and parts[2] else None
        if principal and token:
            mapping[token] = (principal, role)
    return mapping


def _parse_scopes_map(raw: str) -> dict[str, str]:
    """Parse ``principal:role`` comma-separated pairs into ``{principal: role}``.

    Used by ``DM_API_SCOPES`` to layer a role onto an already-registered
    principal (e.g. one declared bare in ``DM_API_KEYS_MAP``). Blank / malformed
    entries are skipped.
    """
    mapping: dict[str, str] = {}
    for chunk in raw.replace("\n", ",").split(","):
        item = chunk.strip()
        if not item or ":" not in item:
            continue
        principal, _, role = item.partition(":")
        principal = principal.strip()
        role = role.strip()
        if principal and role:
            mapping[principal] = role
    return mapping


@dataclass
class TokenStore:
    """In-memory registry of valid API tokens -> :class:`Principal` (PRD Step 7).

    Construct it explicitly with ``tokens=`` (a ``{token: principal_name}`` map)
    or, preferably, from the environment/Settings via :meth:`from_settings` /
    :meth:`from_env`. The store is **open when empty**: :attr:`enabled` is False
    until at least one token is registered, which is what keeps auth off by
    default so existing API tests pass unchanged.
    """

    # token -> principal name
    _tokens: dict[str, str] = field(default_factory=dict)
    # principal name -> scopes
    _scopes: dict[str, frozenset[str]] = field(default_factory=dict)
    # principal name -> RBAC role (the role the scopes were expanded from)
    _roles: dict[str, str] = field(default_factory=dict)
    #: True once any token was registered with an explicit RBAC role/scope, which
    #: is the signal the transport layer uses to *enforce* RBAC. A store built from
    #: plain ``DM_API_KEYS`` (no roles) leaves this False -> role-flat, so existing
    #: auth tests/deployments behave exactly as before.
    rbac_enabled: bool = False

    # ------------------------------------------------------------------ factories
    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "TokenStore":
        """Build from process env (``DM_API_KEYS`` / ``DM_API_KEYS_MAP`` / ``DM_API_SCOPES``).

        All sources merge; the named-map form wins on collisions so an operator
        can give a memorable principal name to a token also listed bare. A third
        ``:role`` field in ``DM_API_KEYS_MAP`` — or a ``DM_API_SCOPES`` entry for
        the principal — turns on RBAC enforcement (:attr:`rbac_enabled`).
        """
        env = os.environ if environ is None else environ
        store = cls()
        for token in _parse_keys_csv(env.get(ENV_API_KEYS, "")):
            store.add(token)  # principal defaults to a fingerprint, role -> default
        for token, (principal, role) in _parse_keys_map(env.get(ENV_API_KEYS_MAP, "")).items():
            store.add(token, principal=principal, role=role)
        # Overlay DM_API_SCOPES roles onto principals registered above.
        for principal, role in _parse_scopes_map(env.get(ENV_API_SCOPES, "")).items():
            store.set_role(principal, role)
        return store

    @classmethod
    def from_settings(cls, settings: Any | None = None) -> "TokenStore":
        """Build from :class:`~disastermind.core.config.Settings`.

        Settings is dependency-light and reads the same env vars at construction;
        we additionally honour optional ``settings.api_keys`` /
        ``settings.api_keys_map`` attributes if a future Settings exposes them
        (forward-compatible — we never *edit* Settings, only read what's there).
        """
        store = cls.from_env()
        if settings is not None:
            raw = getattr(settings, "api_keys", None)
            if isinstance(raw, str):
                for token in _parse_keys_csv(raw):
                    store.add(token)
            raw_map = getattr(settings, "api_keys_map", None)
            if isinstance(raw_map, str):
                for token, (principal, role) in _parse_keys_map(raw_map).items():
                    store.add(token, principal=principal, role=role)
            raw_scopes = getattr(settings, "api_scopes", None)
            if isinstance(raw_scopes, str):
                for principal, role in _parse_scopes_map(raw_scopes).items():
                    store.set_role(principal, role)
        return store

    # --------------------------------------------------------------------- mutate
    def add(
        self,
        token: str,
        principal: str | None = None,
        scopes: frozenset[str] | set[str] | None = None,
        role: str | None = None,
    ) -> Principal:
        """Register ``token``; returns the :class:`Principal` it authenticates.

        ``role`` (one of ``viewer``/``operator``/``admin``) expands to a
        hierarchical scope set and **turns on RBAC enforcement** for the whole
        store. An explicit ``scopes=`` also enables RBAC. Passing neither leaves
        the principal role-flat (default-access) so an unconfigured store and the
        existing auth tests are unchanged.
        """
        token = token.strip()
        if not token:
            raise ValueError("empty token")
        name = principal or f"key-{_fingerprint(token)}"
        self._tokens[token] = name
        if role is not None:
            self.set_role(name, role)
        elif scopes:
            self._scopes[name] = frozenset(scopes)
            self.rbac_enabled = True
        return Principal(
            name=name, fingerprint=_fingerprint(token), scopes=self._scopes.get(name, frozenset())
        )

    def set_role(self, principal: str, role: str) -> None:
        """Assign an RBAC ``role`` to ``principal`` and enable RBAC enforcement.

        Expands the role to its hierarchical scope set (:func:`role_to_scopes`).
        Idempotent; later calls override an earlier role for the same principal.
        """
        self._roles[principal] = role.strip().lower()
        self._scopes[principal] = role_to_scopes(role)
        self.rbac_enabled = True

    # ----------------------------------------------------------------- introspect
    @property
    def enabled(self) -> bool:
        """True when at least one token is configured -> auth is enforced.

        When False the store is *open* (auth disabled) so existing API routes and
        tests behave exactly as before configuration (PRD Step 7 opt-in).
        """
        return bool(self._tokens)

    def __len__(self) -> int:
        return len(self._tokens)

    def role_of(self, principal: str) -> str | None:
        """Return the RBAC role assigned to ``principal`` (or ``None``)."""
        return self._roles.get(principal)

    def verify(self, token: str | None) -> Principal | None:
        """Return the :class:`Principal` for ``token`` or ``None`` if invalid.

        Comparison is constant-time (``hmac.compare_digest``) per registered
        token to resist timing side-channels on the secret.
        """
        if not token:
            return None
        token = token.strip()
        for known, name in self._tokens.items():
            if hmac.compare_digest(known, token):
                return Principal(
                    name=name,
                    fingerprint=_fingerprint(known),
                    scopes=self._scopes.get(name, frozenset()),
                )
        return None


def extract_bearer(authorization: str | None) -> str | None:
    """Pull the raw token out of an ``Authorization`` header value.

    Accepts ``Bearer <token>`` (case-insensitive scheme) and a bare token; both
    are common for API keys. Returns ``None`` for an empty/blank header.
    """
    if not authorization:
        return None
    value = authorization.strip()
    if not value:
        return None
    if value.lower().startswith(BEARER_PREFIX.lower()):
        return value[len(BEARER_PREFIX):].strip() or None
    return value


def authenticate(token: str | None, store: TokenStore | None = None) -> Principal | None:
    """Verify ``token`` against ``store`` -> :class:`Principal` | ``None``.

    The public, framework-agnostic entry point (PRD Step 7). When ``store`` is
    ``None`` we build one from the environment. If the resolved store is *not*
    enabled (no keys configured) every request is allowed and a deterministic
    anonymous principal is returned, so unconfigured deployments stay open.
    """
    store = store if store is not None else TokenStore.from_env()
    if not store.enabled:
        return Principal(name="anonymous", fingerprint="anonymous")
    return store.verify(extract_bearer(token) if token and " " in (token or "") else token) \
        if False else store.verify(_normalise(token))


def _normalise(token: str | None) -> str | None:
    """Accept either a raw token or a full ``Authorization`` header value."""
    if token is None:
        return None
    # If it looks like a header ("Bearer x"), strip the scheme; else pass through.
    if token.lower().startswith(BEARER_PREFIX.lower()):
        return extract_bearer(token)
    return token.strip() or None


class AuthError(Exception):
    """Raised by :func:`require_auth`-wrapped methods on auth failure.

    Carries an HTTP-friendly :attr:`status_code` (401) so a transport layer can
    map it without importing this module's internals.
    """

    status_code = 401

    def __init__(self, message: str = "unauthorized") -> None:
        super().__init__(message)


def require_auth(service: Any, store: TokenStore | None = None) -> "AuthGuard":
    """Wrap a service so its public methods require a valid token (PRD Step 7).

    Framework-agnostic factory. Returns an :class:`AuthGuard` that proxies the
    underlying ``service`` (e.g. a :class:`~disastermind.api.service.DashboardService`).
    Call :meth:`AuthGuard.authorize(token)` once per request to obtain a
    :class:`Principal`-bound view; any attribute access on the guard *without*
    first authorising raises :class:`AuthError` when the store is enabled.

    When the store is not enabled (no keys configured) the guard is transparent —
    every call passes straight through, keeping the dashboard open by default.
    """
    return AuthGuard(service=service, store=store if store is not None else TokenStore.from_env())


@dataclass
class AuthGuard:
    """A token-gated proxy around a service object (framework-agnostic).

    Use :meth:`authorize` to validate a token and receive a :class:`Principal`;
    use the guard directly (attribute access / call) and it enforces that a
    principal was supplied for the current request via :meth:`authorize`.
    """

    service: Any
    store: TokenStore

    @property
    def enabled(self) -> bool:
        """Whether auth is being enforced (mirrors :attr:`TokenStore.enabled`)."""
        return self.store.enabled

    def authorize(self, token: str | None) -> Principal:
        """Validate ``token`` -> :class:`Principal`; raise :class:`AuthError` on failure.

        When the store is disabled this always succeeds with an anonymous
        principal so unconfigured deployments are never blocked.
        """
        principal = authenticate(token, self.store)
        if principal is None:
            raise AuthError("invalid or missing API token")
        return principal

    def call(self, token: str | None, method: str, *args: Any, **kwargs: Any) -> Any:
        """Authorise ``token`` then invoke ``service.<method>(*args, **kwargs)``.

        The convenience path a transport adapter uses per request: a single call
        both checks the token and dispatches to the wrapped service.
        """
        self.authorize(token)
        fn = getattr(self.service, method)
        return fn(*args, **kwargs)


def fastapi_auth_dependency(store: TokenStore | None = None) -> Callable[..., Any]:
    """Return a FastAPI dependency that yields the authenticated :class:`Principal`.

    OPTIONAL and LAZY (HARD RULE 2): FastAPI is imported *inside* this function so
    importing :mod:`disastermind.security.auth` never requires it. The returned
    dependency:

    * is **open** when no keys are configured (returns the anonymous principal),
      so wiring it onto existing routes does not break unconfigured deployments;
    * raises ``HTTPException(401)`` on a missing/invalid token when keys exist.

    Mount it on a route with ``Depends(fastapi_auth_dependency())`` — this module
    never touches ``api/*`` itself; adoption is the server's choice.
    """
    resolved = store if store is not None else TokenStore.from_env()
    try:
        from fastapi import Header, HTTPException
    except Exception as exc:  # pragma: no cover - exercised only without FastAPI
        raise RuntimeError(
            "FastAPI is not installed; install 'fastapi' to use the auth "
            "dependency, or use require_auth()/authenticate() (stdlib only)."
        ) from exc

    def _dependency(authorization: str | None = Header(default=None)) -> Principal:
        principal = authenticate(authorization, resolved)
        if principal is None:
            raise HTTPException(status_code=401, detail="invalid or missing API token")
        return principal

    return _dependency
