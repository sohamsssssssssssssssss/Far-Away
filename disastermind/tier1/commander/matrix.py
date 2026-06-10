"""Autonomy Threshold Matrix and the pure ``classify`` decision function.

PRD Step 7 — the Commander reviews every Tier-2 field order against a matrix of
escalation triggers. The matrix is expressed as data so it is trivial to unit
test and to audit:

    EscalationTrigger -> AutonomyRule(requires_human, human_only, timeout_seconds)

``classify(order, ...)`` is a *pure* function (no I/O, no clock, no bus): given a
field-order dict it returns a :class:`Decision` describing whether the order may
be dispatched autonomously, must wait for human approval, or may never be acted
on by the agent alone (``human_only``).

The classifier recognises an escalation trigger from two independent sources so
it interoperates regardless of how the upstream field agent populates the
payload (loose coupling, PRD Group A wiring contract):

  1. An explicit ``escalation`` block on the FIELD_ORDER payload, e.g.
     ``{"trigger": "mandatory_evacuation_gt_10000", "summary": ..., "scale": N}``.
  2. Heuristic inference from the order contents (keywords in ``reason`` /
     mass-evacuation ``scale``) so a field agent that omits an explicit trigger
     still gets the correct human-in-the-loop treatment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.contracts import (
    EscalationTrigger,
    HUMAN_ONLY_TRIGGERS,
    Priority,
)

# Threshold above which a mandatory evacuation becomes a mass-evacuation that
# requires human sign-off (PRD Step 7: "mandatory evacuation > 10000").
MASS_EVACUATION_THRESHOLD = 10_000


@dataclass(frozen=True)
class AutonomyRule:
    """One row of the Autonomy Threshold Matrix (PRD Step 7)."""

    requires_human: bool
    human_only: bool
    timeout_seconds: int  # 0 == use the agent default

    def auto_executable(self) -> bool:
        """True iff the agent may eventually act without a human.

        Human-only triggers are *never* auto-executable; ordinary escalations
        auto-execute once their timeout lapses.
        """
        return not self.human_only


def build_matrix(default_timeout: int) -> dict[EscalationTrigger, AutonomyRule]:
    """The Autonomy Threshold Matrix as data (PRD Step 7).

    Escalation (human approval, auto-execute on timeout):
      cross-state resource, military asset, mandatory evacuation > 10000,
      requisition private infrastructure, media broadcast.

    Human-only (never autonomous, even on timeout):
      international aid, declare state of emergency, deploy armed forces in
      civil situations, critical national infrastructure.
    """
    matrix: dict[EscalationTrigger, AutonomyRule] = {}
    for trig in EscalationTrigger:
        human_only = trig in HUMAN_ONLY_TRIGGERS
        matrix[trig] = AutonomyRule(
            requires_human=True,  # every listed trigger needs at least review
            human_only=human_only,
            timeout_seconds=default_timeout,
        )
    return matrix


@dataclass
class Decision:
    """Result of classifying one field order (PRD Step 7).

    ``autonomous``     -> dispatch immediately, no hold.
    ``requires_human`` -> escalate; if not human_only, auto-execute on timeout.
    ``human_only``     -> escalate; the agent must NEVER act alone.
    """

    autonomous: bool
    requires_human: bool
    human_only: bool
    trigger: EscalationTrigger | None
    timeout_seconds: int
    reasoning: list[str] = field(default_factory=list)

    @property
    def auto_execute_on_timeout(self) -> bool:
        return self.requires_human and not self.human_only


# --------------------------------------------------------------------------- infer
# Keyword heuristics map free-text order reasons onto escalation triggers when
# the upstream agent did not set an explicit trigger. Order matters: human-only
# triggers are checked first so the most restrictive classification wins.
_KEYWORD_TRIGGERS: list[tuple[EscalationTrigger, tuple[str, ...]]] = [
    (EscalationTrigger.INTERNATIONAL_AID, ("international aid", "foreign aid", "un team", "overseas")),
    (
        EscalationTrigger.STATE_OF_EMERGENCY,
        ("state of emergency", "declare emergency", "national emergency"),
    ),
    (
        EscalationTrigger.ARMED_FORCES_CIVIL,
        ("armed forces", "shoot", "lethal", "armed troops", "open fire"),
    ),
    (
        EscalationTrigger.CRITICAL_NATIONAL_INFRA,
        ("nuclear", "national grid", "critical national", "dam release", "refinery"),
    ),
    (
        EscalationTrigger.CROSS_STATE_RESOURCE,
        ("cross-state", "cross state", "inter-state", "interstate", "neighbouring state",
         "neighboring state", "another state"),
    ),
    (
        EscalationTrigger.MILITARY_ASSET,
        ("military", "army", "navy", "air force", "iaf", "helicopter sortie", "military asset"),
    ),
    (
        EscalationTrigger.REQUISITION_PRIVATE,
        ("requisition", "commandeer", "seize private", "private infrastructure",
         "private vehicles", "private boats"),
    ),
    (
        EscalationTrigger.MEDIA_BROADCAST,
        ("media broadcast", "broadcast", "press release", "public broadcast", "tv alert"),
    ),
]


def _infer_trigger(order: dict[str, Any]) -> EscalationTrigger | None:
    """Best-effort inference of an escalation trigger from order contents.

    Pure and deterministic — used as a fallback when the field agent omits an
    explicit ``escalation`` block (PRD Step 7 robustness).
    """
    # 1) explicit per-order trigger value
    raw = order.get("escalation_trigger") or order.get("trigger")
    trig = _coerce_trigger(raw)
    if trig is not None:
        return trig

    # 2) mass-evacuation by scale
    scale = _as_int(order.get("scale")) or _as_int(order.get("population")) or _as_int(
        order.get("evacuees")
    )
    reason = str(order.get("reason", "")).lower()
    is_evac = any(k in reason for k in ("evacuat", "mandatory evac", "clear zone"))
    if is_evac and scale is not None and scale > MASS_EVACUATION_THRESHOLD:
        return EscalationTrigger.MASS_EVACUATION
    if scale is not None and scale > MASS_EVACUATION_THRESHOLD and "evac" in reason:
        return EscalationTrigger.MASS_EVACUATION

    # 3) keyword inference over the human-readable reason
    haystack = " ".join(
        str(order.get(k, "")) for k in ("reason", "note", "summary", "action")
    ).lower()
    for candidate, keywords in _KEYWORD_TRIGGERS:
        if any(kw in haystack for kw in keywords):
            return candidate
    return None


def _coerce_trigger(raw: Any) -> EscalationTrigger | None:
    if raw is None:
        return None
    if isinstance(raw, EscalationTrigger):
        return raw
    try:
        return EscalationTrigger(str(raw))
    except ValueError:
        # tolerate enum *name* (e.g. "MASS_EVACUATION") as well as its value
        try:
            return EscalationTrigger[str(raw)]
        except (KeyError, ValueError):
            return None


def _as_int(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- classify
def classify(
    order: dict[str, Any],
    matrix: dict[EscalationTrigger, AutonomyRule],
    default_timeout: int,
    escalation: dict[str, Any] | None = None,
) -> Decision:
    """Pure decision function (PRD Step 7).

    Given one field-order dict (and the optional payload-level ``escalation``
    block), determine whether it falls within autonomous authority.

    * No recognised trigger  -> ``autonomous`` (dispatch immediately).
    * Recognised trigger      -> ``requires_human``; ``human_only`` if the trigger
      is in :data:`HUMAN_ONLY_TRIGGERS`.

    No side effects, no clock — easy to unit test.
    """
    reasoning: list[str] = []

    # Prefer the payload-level escalation block if one was supplied …
    trig = _coerce_trigger((escalation or {}).get("trigger")) if escalation else None
    if trig is not None:
        reasoning.append(f"escalation block declared trigger={trig.value}")
    else:
        # … otherwise infer from the order itself.
        trig = _infer_trigger(order)
        if trig is not None:
            reasoning.append(f"inferred trigger={trig.value} from order contents")

    if trig is None:
        reasoning.append("no escalation trigger; within autonomous authority -> dispatch")
        return Decision(
            autonomous=True,
            requires_human=False,
            human_only=False,
            trigger=None,
            timeout_seconds=0,
            reasoning=reasoning,
        )

    rule = matrix.get(trig) or AutonomyRule(
        requires_human=True,
        human_only=trig in HUMAN_ONLY_TRIGGERS,
        timeout_seconds=default_timeout,
    )
    timeout = rule.timeout_seconds or default_timeout
    if rule.human_only:
        reasoning.append(
            f"{trig.value} is HUMAN-ONLY — never auto-executed; await human approval"
        )
    else:
        reasoning.append(
            f"{trig.value} requires human approval; auto-execute after {timeout}s if no response"
        )
    return Decision(
        autonomous=False,
        requires_human=rule.requires_human,
        human_only=rule.human_only,
        trigger=trig,
        timeout_seconds=timeout,
        reasoning=reasoning,
    )


def order_priority(order: dict[str, Any]) -> Priority:
    """Map an order's numeric priority onto the message :class:`Priority` enum."""
    p = _as_int(order.get("priority"))
    if p is None:
        return Priority.HIGH
    p = max(1, min(5, p))
    return Priority(p)
