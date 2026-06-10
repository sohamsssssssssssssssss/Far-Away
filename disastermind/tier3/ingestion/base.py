"""Tier 3 feed-ingestion base agent (PRD Step 2).

Edge-tier adapters poll external public hazard feeds (IMD, CWC India-WRIS,
USGS, NASA FIRMS, ISRO Bhuvan, Open-Meteo, OpenWeatherMap, NCS) and republish
normalised observations onto :data:`Topic.RAW_FEED`. They have **no decision
authority** (PRD Step 2 / Step 8): they observe and report only — the
prediction tier (Tier 2) interprets the raw signal.

Every adapter exposes three pure, side-effect-free helpers so the package
imports and the test-suite runs with stdlib only (PRD Step 10, graceful
degradation):

  * :meth:`parse(raw)`  — decode a provider payload into normalised dicts
                          (and/or a :class:`DisasterEvent`).
  * :meth:`sample()`    — realistic offline fixture mirroring the live schema.
  * :meth:`fetch()`     — the real network GET; lazily imports ``httpx`` /
                          ``feedparser`` and is **never** exercised in tests.

``tick()`` pulls a batch (via :meth:`fetch` when live, :meth:`sample` in the
default degraded mode) and emits one :class:`Message` per batch on
``Topic.RAW_FEED``. Activation thresholds from PRD Step 1 (e.g. USGS M4.5+,
river gauge > 75 % of danger level) decide ALERT vs INFO message type.
"""
from __future__ import annotations

import logging
from typing import Any

from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from ...audit.decision_log import DecisionLogger

log = logging.getLogger("disastermind.ingestion")


class BaseFeedAgent(BaseAgent):
    """Common scaffolding for every external feed adapter (PRD Step 2).

    Subclasses set :attr:`feed_name`, :attr:`module`, and implement
    :meth:`parse`, :meth:`sample`, :meth:`fetch`, plus :meth:`assess` which
    decides — per parsed batch — whether an activation threshold (PRD Step 1)
    has been breached and the priority/reasoning that go with it.
    """

    #: Tier 3 edge agents observe & report only — no autonomous decisions.
    tier: Tier = Tier.EDGE
    decision_authority: bool = False

    #: subclasses override
    feed_name: str = "feed"
    module: Module = Module.ALL
    #: poll only every N ticks (feeds have different natural cadences, PRD Step 2)
    poll_every_ticks: int = 1

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        settings: Any = None,
        live: bool = False,
        name: str | None = None,
    ) -> None:
        # Ingestion agents are producers; they subscribe to nothing inbound.
        super().__init__(
            name=name or f"ingest.{self.feed_name}",
            bus=bus,
            logger=logger,
            subscriptions=[],
        )
        self.settings = settings
        #: ``live`` enables real network fetches; tests/degraded mode keep it
        #: ``False`` so :meth:`sample` fixtures are used (PRD Step 10).
        self.live = live
        self._tick_count = 0

    # ------------------------------------------------------------------ hooks
    def handle(self, message: Message) -> list[Message]:
        """Ingestion adapters are pure producers — no inbound handling."""
        return []

    # ---------------------------------------------------- subclass contract
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Normalise a provider payload into a list of observation dicts.

        Pure: no I/O. Override in every concrete adapter (PRD Step 2).
        """
        raise NotImplementedError

    def sample(self) -> Any:
        """Return realistic offline fixture data (provider-native shape).

        Used by :meth:`tick` whenever ``live`` is ``False`` so the pipeline
        flows end-to-end without network access (PRD Step 10).
        """
        raise NotImplementedError

    def fetch(self, transport: Any = None) -> Any:  # pragma: no cover - network path
        """Perform the real network GET. Lazily imports ``httpx`` (urllib fallback).

        ``transport`` is an injectable ``(url, timeout) -> (status, text)`` seam
        used **only by tests** with a recorded fixture; production passes
        ``None`` so the real network transport is used. Falls back to
        :meth:`sample` on any failure so a flaky feed degrades gracefully rather
        than crashing the edge node (PRD Step 10).
        """
        return self.sample()

    # ------------------------------------------------------------- poll_once
    def poll_once(self, live: bool = False, transport: Any = None) -> list[dict[str, Any]]:
        """Acquire and normalise one batch of observations (PRD Step 2).

        The explicit seam between offline and live ingestion:

          * ``live=False`` (the DEFAULT, and what :meth:`tick` uses in degraded
            mode) returns ``parse(sample())`` — fully offline, deterministic, no
            network. This is the path the test-suite exercises.
          * ``live=True`` performs the real GET via :meth:`fetch` and parses the
            response: ``parse(fetch(transport))``. ``transport`` is injected only
            by tests (a recorded-fixture stub); production leaves it ``None`` so
            the real network transport is used.

        Any live failure degrades to the offline ``sample()`` so a flaky or
        unreachable feed never crashes the edge node (PRD Step 10).
        """
        if not live:
            return self.parse(self.sample())
        try:  # pragma: no cover - network path excluded from tests
            raw = self.fetch(transport=transport)
        except Exception:  # pragma: no cover
            log.exception("%s live poll failed; degrading to sample()", self.name)
            raw = self.sample()
        try:
            return self.parse(raw)
        except Exception:
            log.exception("%s failed to parse live batch; returning empty", self.name)
            return []

    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        """Decide whether an activation threshold (PRD Step 1) is breached.

        Returns ``(is_alert, priority, reasoning)``. Default: informational.
        """
        return False, Priority.INFO, [f"{self.feed_name}: routine observation"]

    # ------------------------------------------------------------------ pull
    def _pull(self) -> Any:
        """Acquire a raw batch: live fetch when enabled, else sample fixture."""
        if not self.live:
            return self.sample()
        try:  # pragma: no cover - network path excluded from tests
            return self.fetch()
        except Exception:  # pragma: no cover
            log.exception("%s live fetch failed; degrading to sample()", self.name)
            return self.sample()

    # ------------------------------------------------------------------ tick
    def tick(self) -> list[Message]:
        """Periodic poll → normalise → emit one RAW_FEED message (PRD Step 2/10).

        Uses the :meth:`poll_once` seam with this agent's ``live`` flag, so the
        offline/live split is identical to a direct ``poll_once`` call. In the
        default degraded mode (``live=False``) this is ``parse(sample())`` — no
        network, fully deterministic.
        """
        self._tick_count += 1
        if self.poll_every_ticks > 1 and (self._tick_count % self.poll_every_ticks) != 0:
            return []

        try:
            observations = self.poll_once(live=self.live)
        except Exception:
            log.exception("%s failed to acquire/parse batch", self.name)
            return []
        if not observations:
            return []

        is_alert, priority, reasoning = self.assess(observations)
        event = self.build_event(observations) if is_alert else None

        payload: dict[str, Any] = {
            "kind": self.feed_name,
            "event": event,
            "observations": observations,
        }
        # A breach is an ALERT; a routine non-breach observation is an
        # informational report the prediction tier ingests. The frozen
        # MessageType taxonomy (core/contracts.py) has no INFO member, so a
        # non-alert observation rides as a QUERY (observe-and-report, Tier 3
        # has no decision authority). Informational priority is still carried
        # via Priority.INFO on ``priority``.
        msg = Message(
            sender=self.name,
            recipient="tier2.prediction",
            type=MessageType.ALERT if is_alert else MessageType.QUERY,
            priority=priority,
            payload=payload,
            reasoning=reasoning,
            topic=Topic.RAW_FEED,
            module=self.module,
            incident_id=(event or {}).get("incident_id") if event else None,
        )
        return [msg]

    # -------------------------------------------------------------- helpers
    def build_event(self, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Build a JSON-able DisasterEvent dict for a breach. Override as needed.

        Default: no structured event (weather/telemetry feeds report raw
        observations; only hazard-detecting feeds mint events).
        """
        return None
