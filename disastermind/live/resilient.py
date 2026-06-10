"""Resilient live feed polling (PRD Step 10 — graceful degradation).

:func:`resilient_poll_feeds` is a hardened wrapper around
:func:`disastermind.live.ingest.poll_feeds` that protects a *live* deployment
from a repeatedly-failing external feed. It adds three things on top of the plain
poll, and only on the live path:

  * **Per-feed circuit breaker.** Each feed's live fetch is wrapped in its own
    :class:`disastermind.ops.CircuitBreaker`. After ``failure_threshold``
    consecutive live failures the breaker OPENS and subsequent polls *short
    circuit* — instead of hammering the sick dependency we degrade straight to
    the offline ``sample()`` (or skip) until the breaker's ``reset_timeout``
    cooldown elapses and it probes again (half-open).
  * **Bounded retry.** A single live attempt is given a couple of immediate
    (zero-delay, injected-sleep) retries before it is counted as a breaker
    failure, smoothing over a transient blip without opening the breaker.
  * **Consecutive-batch de-duplication.** Identical consecutive observation
    batches from the same feed (by content hash) are dropped instead of
    re-emitted, so a feed that keeps returning the same snapshot does not spam
    ``Topic.RAW_FEED`` with duplicates.

The key subtlety: every Tier-3 adapter's own ``fetch()`` already swallows network
errors and degrades to ``sample()`` internally (PRD Step 10), so a failing fetch
never raises out of the agent — the breaker would never see a failure. To let the
breaker actually observe live failures we wrap the *transport* itself in a
sentinel that raises on a non-2xx status or transport error, run the agent's live
``poll_once`` through that wrapped transport **inside** the breaker, and only then
fall back to ``sample()``. The breaker thus trips on real upstream failure while
the runtime still degrades gracefully.

DEFAULT BEHAVIOUR (``live=False``) is completely inert: this function delegates
straight to :func:`poll_feeds`, arming no breakers and changing nothing — the
existing offline test-suite path is byte-for-byte unchanged. If the ``ops``
package is unavailable for any reason, the function also falls back to plain
:func:`poll_feeds`. Stdlib-only, deterministic, no network (HARD RULE 2).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

from .ingest import _agents_of, _build_raw_feed_message, is_feed_agent, poll_feeds

log = logging.getLogger("disastermind.live.resilient")

#: Defaults chosen for a live feed cadence: trip after a few consecutive
#: failures, cool down for a short window before probing again. All overridable
#: by passing pre-built breakers via ``breakers=``.
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_RESET_TIMEOUT = 60.0
#: Immediate (zero-delay) in-attempt retries before a live attempt is declared a
#: breaker failure. Sleep is injected as a no-op so tests never actually wait.
DEFAULT_RETRY_ATTEMPTS = 2


class _LiveFetchError(BaseException):
    """Raised inside the breaker when a live fetch genuinely failed.

    Subclasses :class:`BaseException` (not :class:`Exception`) **on purpose**:
    every Tier-3 adapter's ``fetch()`` wraps its transport call in
    ``except Exception: return self.sample()``, silently degrading on any error.
    If this were an ``Exception`` the adapter would swallow it and the breaker
    would never see the failure (it would record the degraded ``sample()`` as a
    success). As a ``BaseException`` it slips past the adapter's ``except
    Exception`` and propagates up to the breaker, which is configured to treat
    *only* this type as a failure — so a genuine upstream failure trips the
    breaker while every ordinary adapter exception still degrades as before.
    """


def _batch_hash(observations: list[dict[str, Any]]) -> str:
    """Content hash of an observation batch for consecutive de-duplication.

    Deterministic and order-insensitive at the top level: the batch is JSON
    encoded with sorted keys (and a stable fallback ``repr`` for any
    non-JSON-able value) so two structurally identical batches hash equal.
    """
    try:
        blob = json.dumps(observations, sort_keys=True, default=repr)
    except Exception:  # pragma: no cover - defensive, repr default rarely fails
        blob = repr(observations)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _raising_transport(transport: Callable[..., Any] | None) -> Callable[[str, float], Any]:
    """Wrap a ``(url, timeout) -> (status, text)`` transport to raise on failure.

    The Tier-3 adapters' ``fetch()`` swallows transport errors and a non-2xx
    status internally (degrading to ``sample()``), which would hide every live
    failure from the breaker. This wrapper re-surfaces those as
    :class:`_LiveFetchError` *before* the adapter can swallow them, so the
    breaker records a true failure. When ``transport`` is ``None`` (production),
    the shared default transport is used and the same raise-on-failure policy
    applies.
    """
    from ..tier3.ingestion.http import default_transport

    base = transport if transport is not None else default_transport

    def wrapped(url: str, timeout: float) -> Any:
        try:
            result = base(url, timeout)
        except Exception as exc:  # network/transport error => breaker failure
            raise _LiveFetchError(f"transport error for {url}: {exc}") from exc
        # A ``(status, text)`` tuple with a non-2xx status is an upstream failure.
        if isinstance(result, tuple) and len(result) == 2:
            status, _text = result
            try:
                ok = 200 <= int(status) < 300
            except (TypeError, ValueError):
                ok = True  # non-numeric status: let the adapter decide
            if not ok:
                raise _LiveFetchError(f"HTTP {status} from {url}")
        return result

    return wrapped


def _live_observations(agent: Any, transport: Callable[..., Any] | None) -> list[dict[str, Any]]:
    """Acquire one live batch, raising :class:`_LiveFetchError` on real failure.

    Drives ``agent.poll_once(live=True, transport=...)`` with a raise-on-failure
    transport so a genuine upstream failure propagates (for the breaker) instead
    of being silently degraded to ``sample()`` by the adapter. A successful live
    fetch returns its parsed observations unchanged.
    """
    raising = _raising_transport(transport)
    try:
        obs = agent.poll_once(live=True, transport=raising)
    except _LiveFetchError:
        raise
    except TypeError:
        # An adapter whose poll_once predates the transport seam: best effort,
        # treat any failure as degraded rather than a breaker trip.
        obs = agent.poll_once(live=True)
    return obs or []


def _new_breaker(clock: Callable[[], float] | None) -> Any | None:
    """Construct a fresh per-feed CircuitBreaker, or ``None`` if ops is absent."""
    try:
        from ..ops import CircuitBreaker
    except Exception:  # pragma: no cover - ops is part of the package
        return None
    kwargs: dict[str, Any] = dict(
        failure_threshold=DEFAULT_FAILURE_THRESHOLD,
        reset_timeout=DEFAULT_RESET_TIMEOUT,
        # Only a real live failure should trip the breaker — not, say, a
        # downstream emit error.
        exceptions=_LiveFetchError,
    )
    if clock is not None:
        kwargs["clock"] = clock
    return CircuitBreaker(**kwargs)


def _retry_live(
    agent: Any,
    transport: Callable[..., Any] | None,
    attempts: int,
) -> list[dict[str, Any]]:
    """Run ``_live_observations`` with bounded, zero-delay retries.

    Uses :func:`disastermind.ops.retry` with an injected no-op sleep so tests
    never wait. Falls back to a plain single call if ``ops.retry`` is somehow
    unavailable. The last :class:`_LiveFetchError` propagates so the breaker can
    record the failure.
    """
    if attempts <= 1:
        return _live_observations(agent, transport)
    try:
        from ..ops import retry
    except Exception:  # pragma: no cover - ops is part of the package
        return _live_observations(agent, transport)

    @retry(attempts=attempts, base_delay=0.0, exceptions=_LiveFetchError, sleep=lambda _d: None)
    def _attempt() -> list[dict[str, Any]]:
        return _live_observations(agent, transport)

    return _attempt()


def _emit_dedup(
    agent: Any,
    observations: list[dict[str, Any]],
    last_hashes: dict[str, str],
) -> int:
    """Build + emit a RAW_FEED for ``observations``, de-duping consecutive batches.

    Returns 1 if a message was emitted, 0 if the batch was empty or an identical
    consecutive batch was suppressed.
    """
    if not observations:
        return 0
    key = getattr(agent, "name", None) or id(agent)
    digest = _batch_hash(observations)
    if last_hashes.get(key) == digest:
        log.debug("%s: identical consecutive batch de-duped (hash %s)", key, digest[:12])
        return 0

    msg = _build_raw_feed_message(agent, observations)
    if msg is None:
        return 0
    try:
        agent.emit(msg)
    except Exception:  # pragma: no cover - defensive: bus/audit failure
        log.exception("%s emit failed; RAW_FEED dropped", getattr(agent, "name", "?"))
        return 0
    # Only record the hash once the emit actually happened, so a dropped emit
    # does not suppress a later identical (successful) batch.
    last_hashes[key] = digest
    return 1


def resilient_poll_feeds(
    loop: Any,
    live: bool = False,
    transport: Any = None,
    breakers: dict[Any, Any] | None = None,
    clock: Callable[[], float] | None = None,
) -> int:
    """Poll every Tier-3 feed once, hardened with circuit breakers + de-dup.

    Parameters
    ----------
    loop:
        A :class:`~disastermind.orchestration.loop.CoordinationLoop` (its
        ``.agents`` are scanned), an iterable of agents, or a single agent —
        exactly what :func:`poll_feeds` accepts.
    live:
        ``False`` (DEFAULT) → fully inert: delegate straight to
        :func:`poll_feeds` (offline ``sample()`` path), arming no breakers and
        leaving existing behaviour unchanged. ``True`` → the hardened live path.
    transport:
        Injectable ``(url, timeout) -> (status, text)`` transport threaded into
        every feed's live fetch. Supplied **only by tests** (a recorded-fixture
        stub); production leaves it ``None`` so the real HTTP transport is used.
        Ignored entirely when ``live=False``.
    breakers:
        Optional ``{feed_key: CircuitBreaker}`` mapping. Pass a persistent dict
        across calls so breaker state survives between polls (this is how a feed
        accumulates consecutive failures and eventually trips). When omitted a
        fresh, per-call mapping is used (breakers do not persist across calls).
        ``feed_key`` is the agent's ``name``.
    clock:
        Injectable ``() -> float`` clock for the per-feed breakers' cooldown, so
        the OPEN→HALF_OPEN transition is testable without wall-clock. Ignored for
        breakers supplied via ``breakers``.

    Returns the number of RAW_FEED messages emitted (de-duped batches and
    short-circuited feeds count as 0).

    On the live path, for each feed:

      1. If its breaker is OPEN (and cooldown not elapsed) the live fetch is
         *short-circuited* — we degrade to the offline ``sample()`` and emit
         (de-duped) without ever touching the network.
      2. Otherwise a bounded-retry live fetch runs *inside* the breaker. Success
         records a breaker success and emits the live batch (de-duped). A genuine
         live failure records a breaker failure (tripping it after the threshold)
         and degrades to ``sample()`` for this cycle.
    """
    if not live:
        # DEFAULT: completely inert — unchanged offline behaviour, no breakers.
        return poll_feeds(loop, live=False, transport=transport)

    # ops is required for the hardened path; if it is somehow missing, degrade to
    # the plain (still-functional) live poll rather than failing.
    try:
        from ..ops import CircuitOpenError  # noqa: F401
    except Exception:  # pragma: no cover - ops ships with the package
        log.warning("ops unavailable; resilient_poll_feeds falling back to poll_feeds")
        return poll_feeds(loop, live=True, transport=transport)
    from ..ops import CircuitOpenError

    if breakers is None:
        breakers = {}
    # Per-feed de-dup memory lives beside the breakers on the same caller-owned
    # dict, so consecutive identical batches are caught across sequential calls
    # exactly when the caller persists ``breakers``. Stored under a reserved
    # sentinel key that can never collide with a feed name (an agent ``name``).
    last_hashes: dict[Any, str] = breakers.setdefault("__last_hashes__", {})  # type: ignore[assignment]

    emitted = 0
    feeds = [a for a in _agents_of(loop) if is_feed_agent(a)]
    for agent in feeds:
        key = getattr(agent, "name", None) or id(agent)
        breaker = breakers.get(key)
        if breaker is None:
            breaker = _new_breaker(clock)
            if breaker is None:  # pragma: no cover - ops present by here
                emitted += poll_feeds([agent], live=True, transport=transport)
                continue
            breakers[key] = breaker

        observations: list[dict[str, Any]]
        try:
            observations = breaker.call(
                _retry_live, agent, transport, DEFAULT_RETRY_ATTEMPTS
            )
        except CircuitOpenError:
            # Breaker OPEN: short-circuit to the offline sample(), no network.
            log.info("%s breaker OPEN; short-circuiting to sample()", key)
            observations = _safe_sample(agent)
        except _LiveFetchError:
            # Live failure recorded by the breaker; degrade to sample() this cycle.
            log.info("%s live fetch failed; degrading to sample()", key)
            observations = _safe_sample(agent)
        except Exception:  # pragma: no cover - defensive
            log.exception("%s unexpected poll error; degrading to sample()", key)
            observations = _safe_sample(agent)

        emitted += _emit_dedup(agent, observations, last_hashes)

    log.info(
        "resilient_poll_feeds(live=%s): %d feed(s), %d RAW_FEED emitted",
        live,
        len(feeds),
        emitted,
    )
    return emitted


def _safe_sample(agent: Any) -> list[dict[str, Any]]:
    """Parse the agent's offline ``sample()`` defensively (never raise)."""
    try:
        return agent.parse(agent.sample()) or []
    except Exception:  # pragma: no cover - defensive
        log.exception("%s sample() degrade failed; skipping", getattr(agent, "name", "?"))
        return []
