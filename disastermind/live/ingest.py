"""Drive Tier-3 feed polling into the live runtime (PRD Step 2 / Step 9/10).

This is the wiring seam that turns the proven, pure ingestion adapters
(:class:`~disastermind.tier3.ingestion.base.BaseFeedAgent` subclasses:
``USGSFeedAgent``, ``OpenMeteoFeedAgent``, ``FIRMSFeedAgent`` …) into a single
runtime action: :func:`poll_feeds` walks the ingestion agents attached to a
:class:`~disastermind.orchestration.loop.CoordinationLoop` (or a bare list of
agents) and makes each one emit a normalised :data:`Topic.RAW_FEED` message from
the chosen source.

Two sources, mirroring the adapter's own ``poll_once`` seam:

  * ``live=False`` (the DEFAULT) — the fully offline ``sample()`` path: each
    agent normalises its committed offline fixture (``parse(sample())``). No
    network, deterministic, exactly what the test-suite exercises.
  * ``live=True`` — the real ``fetch()->parse()`` path. ``transport`` is an
    injectable ``(url, timeout) -> (status, text)`` callable supplied **only by
    tests** (a recorded-fixture stub); production passes ``None`` so the shared
    HTTP transport (lazy ``httpx`` with a stdlib ``urllib`` fallback) is used.
    Any live failure degrades to the offline ``sample()`` so an unreachable feed
    never crashes the runtime (PRD Step 10).

:func:`poll_feeds` is *inert by default* at the system level: nothing calls it
unless a deployment opts in (see ``LiveSystem.build(live_feeds=...)`` /
``LiveSystem.poll_live()``), so the existing offline test-suite is unchanged and
no test path can reach a real socket.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from ..core.contracts import Message, MessageType, Topic

log = logging.getLogger("disastermind.live.ingest")


def _agents_of(loop_or_agents: Any) -> list[Any]:
    """Return the agent list from a ``CoordinationLoop``-like object or iterable.

    Accepts a loop (anything exposing ``.agents``), a bare list/iterable of
    agents, or a single agent. Never raises — an unrecognised input yields an
    empty roster so the caller simply polls nothing.
    """
    if loop_or_agents is None:
        return []
    agents = getattr(loop_or_agents, "agents", None)
    if agents is not None:
        return list(agents)
    if isinstance(loop_or_agents, (list, tuple, set)):
        return list(loop_or_agents)
    if isinstance(loop_or_agents, Iterable) and not _is_agent(loop_or_agents):
        return list(loop_or_agents)
    return [loop_or_agents]


def _is_agent(obj: Any) -> bool:
    """Heuristic: a single agent exposes ``emit`` + ``name`` (not an iterable)."""
    return hasattr(obj, "emit") and hasattr(obj, "name")


def is_feed_agent(agent: Any) -> bool:
    """True for a Tier-3 ingestion adapter we can poll for RAW_FEED.

    Duck-typed against the stable ``BaseFeedAgent`` surface so we never import
    the concrete classes here: a pollable feed agent exposes ``poll_once``,
    ``sample``, ``parse``, ``assess`` and an ``emit`` egress. We also require
    ``decision_authority is False`` (Tier-3 observe-and-report) to avoid ever
    treating a decision-making agent as a feed.
    """
    if getattr(agent, "decision_authority", True):
        return False
    return all(
        callable(getattr(agent, attr, None))
        for attr in ("poll_once", "sample", "parse", "assess", "emit")
    )


def _build_raw_feed_message(agent: Any, observations: list[dict[str, Any]]) -> Message | None:
    """Build the RAW_FEED message for ``observations`` (mirrors ``tick()``).

    Reuses the agent's own ``assess`` / ``build_event`` so the alert/priority
    semantics and event minting are identical to the agent's periodic ``tick``;
    only the *source* of the observations (sample vs. live fetch) differs.
    """
    if not observations:
        return None
    try:
        is_alert, priority, reasoning = agent.assess(observations)
    except Exception:
        log.exception("%s assess() failed; emitting routine observation", getattr(agent, "name", "?"))
        is_alert, priority, reasoning = False, None, [f"{getattr(agent, 'feed_name', 'feed')}: observation"]

    event = None
    if is_alert:
        try:
            event = agent.build_event(observations)
        except Exception:  # pragma: no cover - defensive
            log.exception("%s build_event() failed; alert without event", getattr(agent, "name", "?"))

    payload: dict[str, Any] = {
        "kind": getattr(agent, "feed_name", "feed"),
        "event": event,
        "observations": observations,
    }
    # Priority.INFO is the sensible default for a non-alert routine report; fall
    # back to it if assess() returned None (defensive path above).
    if priority is None:
        from ..core.contracts import Priority

        priority = Priority.INFO

    return Message(
        sender=getattr(agent, "name", "ingest"),
        recipient="tier2.prediction",
        type=MessageType.ALERT if is_alert else MessageType.QUERY,
        priority=priority,
        payload=payload,
        reasoning=reasoning,
        topic=Topic.RAW_FEED,
        module=getattr(agent, "module", None),
        incident_id=(event or {}).get("incident_id") if event else None,
    )


def poll_feed(agent: Any, *, live: bool = False, transport: Any = None) -> int:
    """Poll one ingestion agent once and emit its RAW_FEED message.

    Returns the number of RAW_FEED messages emitted (0 or 1). Drives the agent's
    ``poll_once`` seam: ``live=False`` uses ``parse(sample())`` (offline DEFAULT);
    ``live=True`` uses ``parse(fetch(transport))`` (real network in prod, an
    injected stub in tests). Never raises — a misbehaving feed yields ``0``.
    """
    try:
        observations = agent.poll_once(live=live, transport=transport)
    except TypeError:
        # An adapter whose poll_once predates the transport seam.
        try:
            observations = agent.poll_once(live=live)
        except Exception:
            log.exception("%s poll_once failed; skipping", getattr(agent, "name", "?"))
            return 0
    except Exception:
        log.exception("%s poll_once failed; skipping", getattr(agent, "name", "?"))
        return 0

    msg = _build_raw_feed_message(agent, observations or [])
    if msg is None:
        return 0
    try:
        agent.emit(msg)
    except Exception:  # pragma: no cover - defensive: bus/audit failure
        log.exception("%s emit failed; RAW_FEED dropped", getattr(agent, "name", "?"))
        return 0
    return 1


def poll_feeds(loop_or_agents: Any, live: bool = False, transport: Any = None) -> int:
    """Poll every Tier-3 ingestion agent once, emitting RAW_FEED (PRD Step 2).

    Parameters
    ----------
    loop_or_agents:
        A :class:`~disastermind.orchestration.loop.CoordinationLoop` (its
        ``.agents`` are scanned), an iterable of agents, or a single agent.
    live:
        ``False`` (DEFAULT) → offline ``sample()`` path for every feed (no
        network, deterministic). ``True`` → real ``fetch()->parse()`` path.
    transport:
        Injectable ``(url, timeout) -> (status, text)`` transport, threaded into
        every feed's ``fetch``. Supplied **only by tests** (a recorded-fixture
        stub); production leaves it ``None`` so the real HTTP transport is used.
        Ignored entirely when ``live=False``.

    Returns the count of RAW_FEED messages emitted across all polled feeds.
    Defensive: a single failing feed is logged and skipped; the rest carry on
    (PRD Step 10 graceful degradation).
    """
    emitted = 0
    feeds = [a for a in _agents_of(loop_or_agents) if is_feed_agent(a)]
    for agent in feeds:
        emitted += poll_feed(agent, live=live, transport=transport)
    log.info("poll_feeds(live=%s): %d feed(s) polled, %d RAW_FEED emitted", live, len(feeds), emitted)
    return emitted
