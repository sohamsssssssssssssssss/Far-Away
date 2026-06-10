"""In-memory token-bucket rate limiting for the Commander Dashboard (PRD Step 7).

The dashboard is the single human-facing surface of an otherwise autonomous
system, so it is the part an operator (or an attacker holding a leaked key) could
hammer. :class:`RateLimiter` caps the request rate **per principal** using the
classic token-bucket algorithm:

* a bucket holds up to ``capacity`` tokens (the burst allowance);
* tokens refill continuously at ``refill_per_second`` (the sustained rate);
* every accepted request spends one token; a request with no token available is
  denied (``allowed=False``) with a ``retry_after`` hint.

It is deliberately dependency-free (stdlib only, HARD RULE 2) and does no I/O —
the clock is injectable so tests are deterministic and there is no real
``time.sleep`` on any test path. Buckets live in a plain dict keyed by the
principal name (:class:`~disastermind.security.auth.Principal.name`), so an
unauthenticated/anonymous deployment shares a single ``"anonymous"`` bucket and a
configured one gets a bucket per key.

This module owns its own constants (HARD RULE 3); it never edits
``core/contracts.py`` or ``core/config.py``.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

# Env knobs (owned by THIS package). Off-path defaults are generous so the
# limiter never trips an existing deployment unless explicitly tuned.
ENV_RATE_CAPACITY = "DM_RATE_CAPACITY"
ENV_RATE_REFILL = "DM_RATE_REFILL_PER_SEC"

#: Default burst capacity (tokens) and sustained refill (tokens/second).
DEFAULT_CAPACITY = 60
DEFAULT_REFILL_PER_SECOND = 60.0

# Per-CLIENT-IP knobs (PRD Step 7 hardening). The IP limiter is the OUTER bound:
# it caps an *unauthenticated* burst from a single source address before token
# auth even runs, so an anonymous flood is bounded independently of any principal
# bucket. Defaults are deliberately generous (a real operator/proxy never trips
# them) yet finite, so a hostile burst cannot be unbounded.
ENV_RATE_IP_CAPACITY = "DM_RATE_IP_CAPACITY"
ENV_RATE_IP_REFILL = "DM_RATE_IP_REFILL_PER_SEC"

#: Default per-IP burst capacity and sustained refill (tokens/second).
DEFAULT_IP_CAPACITY = 120
DEFAULT_IP_REFILL_PER_SECOND = 120.0


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass
class _Bucket:
    """A single principal's token bucket (mutable internal state)."""

    tokens: float
    last_refill: float

    def _refill(self, now: float, capacity: float, rate: float) -> None:
        """Add tokens accrued since :attr:`last_refill`, clamped to ``capacity``."""
        if now <= self.last_refill:
            # Clock did not advance (or went backwards): nothing to add.
            self.last_refill = max(self.last_refill, now)
            return
        elapsed = now - self.last_refill
        self.tokens = min(capacity, self.tokens + elapsed * rate)
        self.last_refill = now


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a single :meth:`RateLimiter.check` call.

    Truthy when the request is allowed, so callers can write ``if limiter.check(p):``.
    """

    allowed: bool
    remaining: float
    retry_after: float = 0.0

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.allowed


@dataclass
class RateLimiter:
    """Per-principal token-bucket limiter (PRD Step 7 dashboard hardening).

    Parameters
    ----------
    capacity:
        Maximum tokens a bucket can hold — the burst allowance and the number of
        immediate requests permitted from a cold bucket.
    refill_per_second:
        Sustained refill rate. After exhausting the burst, a principal may make
        roughly this many requests per second.
    clock:
        Monotonic-ish time source (seconds, float). Injectable for deterministic
        tests; defaults to :func:`time.monotonic`. Never sleeps.

    The limiter is thread-safe (a single lock guards bucket creation/mutation),
    matching a multi-worker dashboard server.
    """

    capacity: int = field(default_factory=lambda: _env_int(ENV_RATE_CAPACITY, DEFAULT_CAPACITY))
    refill_per_second: float = field(
        default_factory=lambda: _env_float(ENV_RATE_REFILL, DEFAULT_REFILL_PER_SECOND)
    )
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[str, _Bucket] = field(default_factory=dict, repr=False)
    _lock: "threading.Lock" = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        if self.refill_per_second < 0:
            raise ValueError("refill_per_second must be non-negative")

    # ------------------------------------------------------------------ core
    def check(self, principal: str, cost: float = 1.0) -> RateLimitResult:
        """Try to spend ``cost`` tokens for ``principal`` without mutating on deny.

        Returns a :class:`RateLimitResult`; truthy iff allowed. This is the only
        method that consumes tokens — :meth:`allow` is a boolean alias.
        """
        key = principal or "anonymous"
        now = float(self.clock())
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.capacity), last_refill=now)
                self._buckets[key] = bucket
            bucket._refill(now, float(self.capacity), self.refill_per_second)
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return RateLimitResult(allowed=True, remaining=bucket.tokens)
            # Denied: report how long until enough tokens accrue.
            if self.refill_per_second > 0:
                deficit = cost - bucket.tokens
                retry_after = deficit / self.refill_per_second
            else:
                retry_after = float("inf")
            return RateLimitResult(
                allowed=False, remaining=bucket.tokens, retry_after=retry_after
            )

    def allow(self, principal: str, cost: float = 1.0) -> bool:
        """Boolean convenience wrapper over :meth:`check`."""
        return self.check(principal, cost).allowed

    # ------------------------------------------------------------- introspect
    def remaining(self, principal: str) -> float:
        """Tokens currently available to ``principal`` (refilled to *now*).

        Read-only: refills the bucket's view of time but never spends a token.
        """
        key = principal or "anonymous"
        now = float(self.clock())
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return float(self.capacity)
            bucket._refill(now, float(self.capacity), self.refill_per_second)
            return bucket.tokens

    def reset(self, principal: str | None = None) -> None:
        """Drop one principal's bucket, or all buckets when ``principal`` is None."""
        with self._lock:
            if principal is None:
                self._buckets.clear()
            else:
                self._buckets.pop(principal, None)


# ----------------------------------------------------------------- per-IP limiting
def ip_rate_limiter(clock: Callable[[], float] = time.monotonic) -> "RateLimiter":
    """Build a :class:`RateLimiter` tuned for per-CLIENT-IP buckets (PRD Step 7).

    Reads the dedicated ``DM_RATE_IP_*`` knobs (separate from the per-principal
    ``DM_RATE_*`` ones) so an operator can bound an unauthenticated burst without
    affecting the per-principal limits. Stdlib-only, clock injectable for tests.
    """
    return RateLimiter(
        capacity=_env_int(ENV_RATE_IP_CAPACITY, DEFAULT_IP_CAPACITY),
        refill_per_second=_env_float(ENV_RATE_IP_REFILL, DEFAULT_IP_REFILL_PER_SECOND),
        clock=clock,
    )


def client_ip(scope: dict | None, *, trust_forwarded: bool = True) -> str:
    """Extract a best-effort client IP key from an ASGI ``scope`` (never raises).

    Resolution order (PRD Step 7):

    * the left-most address in ``X-Forwarded-For`` (the original client behind a
      trusted reverse proxy / load balancer), when ``trust_forwarded`` — Railway,
      Heroku and most ingress controllers set this;
    * else the ASGI ``client`` tuple's host;
    * else the literal ``"unknown"`` so a missing address still shares one bucket
      (bounded) rather than being unlimited.

    The returned string is only ever used as an in-memory bucket key, so a spoofed
    ``X-Forwarded-For`` can at worst evade *its own* bucket — it can never amplify
    against another client, and the per-principal limiter still applies.
    """
    if not scope:
        return "unknown"
    try:
        headers = scope.get("headers") or []
        if trust_forwarded:
            for k, v in headers:
                if k == b"x-forwarded-for" or k == "x-forwarded-for":
                    raw = v.decode("latin-1") if isinstance(v, (bytes, bytearray)) else str(v)
                    first = raw.split(",")[0].strip()
                    if first:
                        return first
        client = scope.get("client")
        if client and isinstance(client, (tuple, list)) and client[0]:
            return str(client[0])
    except Exception:  # pragma: no cover - extraction must never raise
        return "unknown"
    return "unknown"
