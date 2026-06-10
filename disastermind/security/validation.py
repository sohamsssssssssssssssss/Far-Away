"""Message-payload validation for the dashboard ingress (PRD Step 7 / Step 9).

The dashboard is the only place a human (or an external integration) can inject a
:class:`~disastermind.core.contracts.Message` into the bus, so payloads arriving
there must be schema-checked before they reach the autonomous tiers. This module
codifies the *payload conventions* each well-known topic uses (discovered from
the producers in ``tier1``/``tier2``/``tier3`` — never edited here, HARD RULE 1)
as a table of required keys, plus light per-topic structural checks.

The single entry point is :func:`validate_message_payload`::

    ok, errors = validate_message_payload(Topic.DISPATCH, payload)

``ok`` is ``True`` with an empty ``errors`` list when the payload satisfies the
convention; otherwise ``ok`` is ``False`` and ``errors`` lists every problem (we
collect *all* errors, not just the first, so an operator fixes the form in one
pass). Validation is purely structural and stdlib-only (HARD RULE 2): it never
imports the tier modules and does no I/O.

Topics it knows about (keyed by the frozen ``Topic`` constants, but a bare topic
*name* string is accepted too):

* ``RAW_FEED``       — ``kind``, ``observations`` (+ optional ``event``)
* ``PREDICTION``     — ``kind``, ``risk_cells``
* ``CASCADE``        — ``kind``, ``failures``
* ``RESOURCE_PLAN``  — ``kind``, ``orders``
* ``ROUTING_PLAN``   — ``kind``, ``routes``
* ``FIELD_ORDER``    — ``kind``, ``orders``
* ``DISPATCH``       — ``channel``, ``recipients``, ``body``

Unknown topics pass (return ``ok=True``) so adding a new topic never breaks the
ingress before its schema is registered here.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# We reference the frozen Topic constants for the canonical names but ALSO accept
# the short logical name (the part after the tier prefix) so callers do not have
# to import Topic. core/contracts.py is FROZEN — we only read it.
try:  # pragma: no cover - import shim; exercised indirectly
    from disastermind.core.contracts import Topic as _Topic

    _RAW_FEED = _Topic.RAW_FEED
    _PREDICTION = _Topic.PREDICTION
    _CASCADE = _Topic.CASCADE
    _RESOURCE_PLAN = _Topic.RESOURCE_PLAN
    _ROUTING_PLAN = _Topic.ROUTING_PLAN
    _FIELD_ORDER = _Topic.FIELD_ORDER
    _DISPATCH = _Topic.DISPATCH
except Exception:  # pragma: no cover - stdlib-only fallback if contracts moves
    _RAW_FEED = "tier3.raw_feed"
    _PREDICTION = "tier2.prediction"
    _CASCADE = "tier2.cascade"
    _RESOURCE_PLAN = "tier2.resource_plan"
    _ROUTING_PLAN = "tier2.routing_plan"
    _FIELD_ORDER = "tier2.field_order"
    _DISPATCH = "tier3.dispatch"


# Short logical aliases an operator might pass instead of the fully-qualified
# topic constant (e.g. "DISPATCH" or "dispatch").
_ALIASES: dict[str, str] = {
    "RAW_FEED": _RAW_FEED,
    "PREDICTION": _PREDICTION,
    "CASCADE": _CASCADE,
    "RESOURCE_PLAN": _RESOURCE_PLAN,
    "ROUTING_PLAN": _ROUTING_PLAN,
    "FIELD_ORDER": _FIELD_ORDER,
    "DISPATCH": _DISPATCH,
}


def _canonical_topic(topic: str) -> str:
    """Map an alias / short name / bare suffix to a canonical Topic constant."""
    if not topic:
        return ""
    if topic in _SCHEMAS:
        return topic
    upper = topic.upper().replace(".", "_")
    if upper in _ALIASES:
        return _ALIASES[upper]
    # Allow the bare suffix after a "tierN." prefix, e.g. "raw_feed".
    suffix = topic.split(".")[-1].upper()
    if suffix in _ALIASES:
        return _ALIASES[suffix]
    return topic


# --------------------------------------------------------------------------- schema
# Required top-level keys per topic, derived from the live producers:
#   RAW_FEED      tier3/ingestion/base.py   -> kind, event, observations
#   PREDICTION    tier2/prediction/agents   -> kind, incident_id, risk_cells, ...
#   CASCADE       tier2/cascade/*           -> kind, incident_id, failures, ...
#   RESOURCE_PLAN tier2/resource/agent.py   -> kind, incident_id, orders, ...
#   ROUTING_PLAN  tier2/routing/agent.py    -> kind, incident_id, routes
#   FIELD_ORDER   tier2/field/agent.py      -> kind, incident_id, orders, escalation
#   DISPATCH      tier1/commander/agent.py  -> channel, recipients, body, ...
#
# We require the *load-bearing* keys (the ones a consumer dereferences), not every
# optional/decorative key, so a minimal valid message is not over-constrained.
_REQUIRED: dict[str, tuple[str, ...]] = {
    _RAW_FEED: ("kind", "observations"),
    _PREDICTION: ("kind", "risk_cells"),
    _CASCADE: ("kind", "failures"),
    _RESOURCE_PLAN: ("kind", "orders"),
    _ROUTING_PLAN: ("kind", "routes"),
    _FIELD_ORDER: ("kind", "orders"),
    _DISPATCH: ("channel", "recipients", "body"),
}

# Keys whose value must be a list when present (structural sanity).
_LIST_KEYS: dict[str, tuple[str, ...]] = {
    _RAW_FEED: ("observations",),
    _PREDICTION: ("risk_cells",),
    _CASCADE: ("failures",),
    _RESOURCE_PLAN: ("orders",),
    _ROUTING_PLAN: ("routes",),
    _FIELD_ORDER: ("orders",),
    _DISPATCH: ("recipients",),
}

# The "kind" discriminator each topic is expected to carry, used as a soft check
# (a mismatch is a warning-level error so a deliberate kind override still flags).
_EXPECTED_KIND: dict[str, frozenset[str]] = {
    _RAW_FEED: frozenset(),  # kind == feed name (free-form); presence is enough
    _PREDICTION: frozenset({"risk"}),
    _CASCADE: frozenset({"cascade"}),
    _RESOURCE_PLAN: frozenset({"resource_plan"}),
    _ROUTING_PLAN: frozenset({"routing"}),
    _FIELD_ORDER: frozenset({"field_order"}),
    _DISPATCH: frozenset(),  # DISPATCH carries no "kind" on the inbound order
}

# The full schema set (used by _canonical_topic to detect already-canonical input).
_SCHEMAS: frozenset[str] = frozenset(_REQUIRED)


def known_topics() -> tuple[str, ...]:
    """Return the canonical topics this validator has a schema for."""
    return tuple(sorted(_REQUIRED))


def _is_listlike(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _check_dispatch(payload: dict[str, Any], errors: list[str]) -> None:
    """Extra structural checks for a DISPATCH order (the highest-stakes topic).

    A DISPATCH is what physically tasks a field team, so we are strict: it must
    name a non-empty string ``channel``, a non-empty ``recipients`` list, and a
    non-empty string ``body``. (Matches tier1/commander/agent.py + the router /
    channel contract in tier3/dispatch/*.)
    """
    channel = payload.get("channel")
    if "channel" in payload and (not isinstance(channel, str) or not channel.strip()):
        errors.append("DISPATCH 'channel' must be a non-empty string")

    recipients = payload.get("recipients")
    if "recipients" in payload:
        if not _is_listlike(recipients):
            errors.append("DISPATCH 'recipients' must be a list")
        elif len(recipients) == 0:
            errors.append("DISPATCH 'recipients' must not be empty")

    body = payload.get("body")
    if "body" in payload and (not isinstance(body, str) or not body.strip()):
        errors.append("DISPATCH 'body' must be a non-empty string")


def _missing(keys: Iterable[str], payload: dict[str, Any]) -> list[str]:
    return [k for k in keys if k not in payload]


def validate_message_payload(
    topic: str, payload: Any
) -> tuple[bool, list[str]]:
    """Validate ``payload`` against the convention for ``topic`` (PRD Step 7).

    Parameters
    ----------
    topic:
        A :class:`~disastermind.core.contracts.Topic` constant, a short alias
        (``"DISPATCH"``), or the bare suffix (``"dispatch"``).
    payload:
        The message ``payload`` dict to check.

    Returns
    -------
    (ok, errors):
        ``ok`` is ``True`` (and ``errors`` empty) when the payload is well-formed.
        On failure ``ok`` is ``False`` and ``errors`` lists *every* problem found.

    Unknown topics are accepted (``True, []``) so the ingress never blocks a topic
    whose schema is not yet registered here.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return False, [f"payload must be a dict, got {type(payload).__name__}"]

    canonical = _canonical_topic(str(topic))
    required = _REQUIRED.get(canonical)
    if required is None:
        # Unknown topic: nothing to enforce yet -> accept.
        return True, []

    # 1. required keys present
    for key in _missing(required, payload):
        errors.append(f"{_label(canonical)} missing required key '{key}'")

    # 2. list-typed keys are actually lists (only when present)
    for key in _LIST_KEYS.get(canonical, ()):  # noqa: PLR2004 - small table
        if key in payload and not _is_listlike(payload[key]):
            errors.append(f"{_label(canonical)} '{key}' must be a list")

    # 3. kind discriminator (when the topic carries one)
    expected_kinds = _EXPECTED_KIND.get(canonical, frozenset())
    if expected_kinds:
        kind = payload.get("kind")
        if kind is not None and kind not in expected_kinds:
            errors.append(
                f"{_label(canonical)} unexpected kind {kind!r}; "
                f"expected one of {sorted(expected_kinds)}"
            )

    # 4. topic-specific structural checks
    if canonical == _DISPATCH:
        _check_dispatch(payload, errors)

    return (not errors), errors


def _label(canonical: str) -> str:
    """Human-friendly short label for a canonical topic (for error messages)."""
    for alias, target in _ALIASES.items():
        if target == canonical:
            return alias
    return canonical
