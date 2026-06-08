"""Tests for the ``disastermind.security`` dashboard-hardening package (PRD Step 7).

Stdlib-only, fully offline, deterministic (HARD RULE 2). Covers:

* token auth: allow a valid key, deny a bad/missing key, and **default-open** when
  no keys are configured (so existing api/* routes/tests are unaffected);
* the framework-agnostic ``require_auth`` guard;
* the token-bucket ``RateLimiter``: trips after N requests then refills with time;
* ``validate_message_payload``: catches a malformed DISPATCH and accepts a good one.
"""
from __future__ import annotations

import pytest

from disastermind.core.contracts import Topic
from disastermind.security import (
    AuthError,
    Principal,
    RateLimiter,
    TokenStore,
    authenticate,
    extract_bearer,
    known_topics,
    require_auth,
    validate_message_payload,
)


# ============================================================== auth: tokens
def test_valid_token_authenticates_to_named_principal() -> None:
    store = TokenStore()
    store.add("s3cret", principal="commander")
    assert store.enabled is True

    principal = authenticate("s3cret", store)
    assert isinstance(principal, Principal)
    assert principal.name == "commander"
    # fingerprint is a short, non-reversible digest — never the raw token.
    assert principal.fingerprint and principal.fingerprint != "s3cret"


def test_bearer_header_form_is_accepted() -> None:
    store = TokenStore()
    store.add("abc123", principal="observer")
    principal = authenticate("Bearer abc123", store)
    assert principal is not None
    assert principal.name == "observer"


def test_bad_and_missing_tokens_are_denied_when_configured() -> None:
    store = TokenStore()
    store.add("good-key", principal="commander")
    assert authenticate("wrong-key", store) is None
    assert authenticate(None, store) is None
    assert authenticate("", store) is None


def test_default_open_when_no_keys_configured() -> None:
    # An empty store is OPEN so existing dashboards/tests are unaffected.
    store = TokenStore()
    assert store.enabled is False

    anon = authenticate(None, store)
    assert anon is not None
    assert anon.name == "anonymous"
    # Even a random token is accepted (it just maps to anonymous) when open.
    assert authenticate("whatever", store) is not None


def test_token_store_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DM_API_KEYS", "k1, k2")
    monkeypatch.setenv("DM_API_KEYS_MAP", "commander:cmd-key")
    store = TokenStore.from_env()
    assert store.enabled is True
    assert authenticate("k1", store) is not None
    assert authenticate("cmd-key", store).name == "commander"
    assert authenticate("absent", store) is None


def test_extract_bearer_variants() -> None:
    assert extract_bearer("Bearer xyz") == "xyz"
    assert extract_bearer("bearer xyz") == "xyz"  # case-insensitive scheme
    assert extract_bearer("rawtoken") == "rawtoken"
    assert extract_bearer(None) is None
    assert extract_bearer("   ") is None


# ============================================================== auth: guard
def _service():
    class _Svc:
        def status(self) -> str:
            return "ok"

    return _Svc()


def test_require_auth_blocks_then_allows_when_configured() -> None:
    store = TokenStore()
    store.add("let-me-in", principal="commander")
    guard = require_auth(_service(), store=store)
    assert guard.enabled is True

    with pytest.raises(AuthError):
        guard.authorize("nope")

    principal = guard.authorize("let-me-in")
    assert principal.name == "commander"
    assert guard.call("let-me-in", "status") == "ok"


def test_require_auth_transparent_when_open() -> None:
    guard = require_auth(_service(), store=TokenStore())
    assert guard.enabled is False
    # No keys configured: any (even missing) token authorises as anonymous.
    assert guard.authorize(None).name == "anonymous"
    assert guard.call(None, "status") == "ok"


# ============================================================== rate limiter
class _FakeClock:
    """Deterministic, advanceable monotonic clock (no real sleep)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_rate_limiter_trips_after_capacity() -> None:
    clock = _FakeClock()
    # 3-token burst, no refill so we can observe a hard trip.
    limiter = RateLimiter(capacity=3, refill_per_second=0.0, clock=clock)

    assert limiter.allow("commander") is True
    assert limiter.allow("commander") is True
    assert limiter.allow("commander") is True
    # 4th request within the same instant -> denied.
    denied = limiter.check("commander")
    assert denied.allowed is False
    assert bool(denied) is False
    assert denied.retry_after == float("inf")  # no refill configured


def test_rate_limiter_refills_over_time() -> None:
    clock = _FakeClock()
    limiter = RateLimiter(capacity=2, refill_per_second=1.0, clock=clock)

    assert limiter.allow("p") is True
    assert limiter.allow("p") is True
    assert limiter.allow("p") is False  # bucket empty

    clock.advance(1.0)  # one token refilled
    assert limiter.allow("p") is True
    assert limiter.allow("p") is False  # spent it again

    clock.advance(10.0)  # would overfill, but capacity caps at 2
    assert limiter.remaining("p") == 2.0
    assert limiter.allow("p") is True
    assert limiter.allow("p") is True
    assert limiter.allow("p") is False


def test_rate_limiter_is_per_principal() -> None:
    clock = _FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_second=0.0, clock=clock)
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is False
    # bob has his own independent bucket.
    assert limiter.allow("bob") is True


def test_rate_limiter_reset() -> None:
    clock = _FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_second=0.0, clock=clock)
    assert limiter.allow("x") is True
    assert limiter.allow("x") is False
    limiter.reset("x")
    assert limiter.allow("x") is True


def test_rate_limiter_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        RateLimiter(capacity=0)
    with pytest.raises(ValueError):
        RateLimiter(capacity=5, refill_per_second=-1.0)


# ============================================================== validation
def _good_dispatch() -> dict:
    return {
        "channel": "field_radio",
        "recipients": ["team-7"],
        "body": "DISPATCH team-7 -> sector A: evacuate",
        "order": {"team": "team-7"},
        "via": "auto",
    }


def test_validate_accepts_good_dispatch() -> None:
    ok, errors = validate_message_payload(Topic.DISPATCH, _good_dispatch())
    assert ok is True
    assert errors == []


def test_validate_catches_malformed_dispatch() -> None:
    bad = {"channel": "", "recipients": [], "body": ""}  # all empty / one missing later
    ok, errors = validate_message_payload(Topic.DISPATCH, bad)
    assert ok is False
    # every problem reported, not just the first
    joined = " ".join(errors)
    assert "channel" in joined
    assert "recipients" in joined
    assert "body" in joined


def test_validate_missing_dispatch_key() -> None:
    missing = {"recipients": ["t1"], "body": "go"}  # no channel
    ok, errors = validate_message_payload(Topic.DISPATCH, missing)
    assert ok is False
    assert any("channel" in e for e in errors)


def test_validate_accepts_alias_and_suffix_topics() -> None:
    payload = _good_dispatch()
    assert validate_message_payload("DISPATCH", payload)[0] is True
    assert validate_message_payload("dispatch", payload)[0] is True


def test_validate_good_resource_plan_and_routing() -> None:
    ok, errors = validate_message_payload(
        Topic.RESOURCE_PLAN, {"kind": "resource_plan", "orders": []}
    )
    assert ok is True and errors == []

    ok, errors = validate_message_payload(
        Topic.ROUTING_PLAN, {"kind": "routing", "routes": []}
    )
    assert ok is True and errors == []


def test_validate_raw_feed_requires_observations() -> None:
    ok, errors = validate_message_payload(
        Topic.RAW_FEED, {"kind": "usgs", "event": None}
    )
    assert ok is False
    assert any("observations" in e for e in errors)

    ok, _ = validate_message_payload(
        Topic.RAW_FEED, {"kind": "usgs", "observations": [{"mag": 6.1}]}
    )
    assert ok is True


def test_validate_wrong_kind_flagged() -> None:
    ok, errors = validate_message_payload(
        Topic.CASCADE, {"kind": "not_cascade", "failures": []}
    )
    assert ok is False
    assert any("kind" in e for e in errors)


def test_validate_non_dict_payload() -> None:
    ok, errors = validate_message_payload(Topic.DISPATCH, ["not", "a", "dict"])
    assert ok is False
    assert errors


def test_validate_unknown_topic_passes() -> None:
    ok, errors = validate_message_payload("tier9.something_new", {"anything": 1})
    assert ok is True
    assert errors == []


def test_known_topics_covers_conventions() -> None:
    topics = known_topics()
    for expected in (
        Topic.RAW_FEED,
        Topic.PREDICTION,
        Topic.CASCADE,
        Topic.RESOURCE_PLAN,
        Topic.ROUTING_PLAN,
        Topic.FIELD_ORDER,
        Topic.DISPATCH,
    ):
        assert expected in topics
