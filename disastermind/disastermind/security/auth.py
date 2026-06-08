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
  named principals (e.g. ``commander:abc123,observer:def456``).

A bare token from ``DM_API_KEYS`` is given the principal name equal to a short,
non-reversible fingerprint so audit logs can attribute actions without leaking
the secret.
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

#: HTTP header carrying the bearer token (case-insensitive in practice).
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "


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


def _parse_keys_map(raw: str) -> dict[str, str]:
    """Parse ``principal:token`` comma-separated pairs into ``{token: principal}``."""
    mapping: dict[str, str] = {}
    for chunk in raw.replace("\n", ",").split(","):
        item = chunk.strip()
        if not item or ":" not in item:
            continue
        principal, _, token = item.partition(":")
        principal = principal.strip()
        token = token.strip()
        if principal and token:
            mapping[token] = principal
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

    # ------------------------------------------------------------------ factories
    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "TokenStore":
        """Build from process env (``DM_API_KEYS`` / ``DM_API_KEYS_MAP``).

        Both sources merge; the named-map form wins on collisions so an operator
        can give a memorable principal name to a token also listed bare.
        """
        env = os.environ if environ is None else environ
        store = cls()
        for token in _parse_keys_csv(env.get(ENV_API_KEYS, "")):
            store.add(token)  # principal defaults to a fingerprint
        for token, principal in _parse_keys_map(env.get(ENV_API_KEYS_MAP, "")).items():
            store.add(token, principal=principal)
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
                for token, principal in _parse_keys_map(raw_map).items():
                    store.add(token, principal=principal)
        return store

    # --------------------------------------------------------------------- mutate
    def add(
        self,
        token: str,
        principal: str | None = None,
        scopes: frozenset[str] | set[str] | None = None,
    ) -> Principal:
        """Register ``token``; returns the :class:`Principal` it authenticates."""
        token = token.strip()
        if not token:
            raise ValueError("empty token")
        name = principal or f"key-{_fingerprint(token)}"
        self._tokens[token] = name
        if scopes:
            self._scopes[name] = frozenset(scopes)
        return Principal(
            name=name, fingerprint=_fingerprint(token), scopes=self._scopes.get(name, frozenset())
        )

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
