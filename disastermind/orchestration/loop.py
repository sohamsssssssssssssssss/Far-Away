"""The 30-second coordination loop and system bootstrap (PRD Group A, Step 10).

``build_system`` wires the whole agent DAG on one bus using the proven
subscriber-before-producer order (so subscriptions exist before any synchronous
in-memory fan-out). Each module is loaded defensively: a module that fails to
import or construct is skipped and the rest carry on — PRD Step 10 graceful
degradation ("if an agent fails, other agents continue independently").

``CoordinationLoop.run_once`` advances the system one cycle *without sleeping*
(deterministic for tests); ``run`` is the real wall-clock loop.
"""
from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field

from ..audit.decision_log import DecisionLogger
from ..core.bus import InMemoryBus, MessageBus
from ..core.config import Settings
from .triggers import Signals, should_activate

log = logging.getLogger("disastermind.loop")

# Subscriber-before-producer load order (mirrors the verified test harness):
# observability (all-topic) + persistence (all-topic) + the LLM narrator subscribe
# first so they see every message; reactive Tier 2/1 + dispatch next; IoT +
# ingestion + social (producers) last.
MODULE_BUILD_PATHS = [
    "disastermind.observability.build",  # metrics collector — subscribes to all topics
    "disastermind.tracing.build",        # trace collector — subscribes all, per-incident latency
    "disastermind.persistence.build",    # state persistor — subscribes all, writes to storage
    "disastermind.llm.build",            # Group B escalation narrator — subscribes ESCALATION
    "disastermind.tier2.prediction.build",
    "disastermind.tier2.cascade.build",
    "disastermind.tier2.resource.build",
    "disastermind.tier2.routing.build",
    "disastermind.tier2.field.build",
    "disastermind.tier1.commander.build",
    "disastermind.tier3.dispatch.build",
    "disastermind.tier3.iot.build",
    "disastermind.tier3.ingestion.build",
    "disastermind.tier3.social.build",   # social-media NLP — Module C RAW_FEED producer
]


@dataclass
class CoordinationLoop:
    bus: MessageBus
    logger: DecisionLogger
    settings: Settings
    agents: list = field(default_factory=list)
    disaster_active: bool = False
    cycle: int = 0
    degraded_modules: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The commander's timeout sweep is driven explicitly with the loop clock
        # so escalation timeouts are deterministic; exclude it from generic ticks.
        self.commander = next((a for a in self.agents if getattr(a, "name", "") == "commander"), None)

    # ------------------------------------------------------------------ cycle
    def run_once(self, now_epoch: float | None = None) -> int:
        """Advance one coordination cycle (PRD Step 10). Never sleeps.

        Order each cycle: re-ingest + re-run every agent's periodic work, then
        sweep the commander's escalation timeouts against ``now_epoch``.
        """
        self.cycle += 1
        for a in self.agents:
            if a is self.commander:
                continue  # resolved explicitly below with the supplied clock
            try:
                a.run_tick()
            except Exception:
                log.exception("agent %s tick failed (continuing)", getattr(a, "name", "?"))
        if self.commander is not None:
            try:
                self.commander.resolve_pending(now_epoch)
            except Exception:
                log.exception("commander timeout sweep failed (continuing)")
        return self.cycle

    def run(self, max_cycles: int | None = None, clock=None, sleep=None) -> int:
        """Real wall-clock loop: tick every ``loop_interval_seconds`` while active.

        ``clock``/``sleep`` are injectable for tests. Returns cycles executed.
        """
        self.disaster_active = True
        sleep = sleep or time.sleep
        n = 0
        while self.disaster_active:
            self.run_once(clock() if clock else None)
            n += 1
            if max_cycles is not None and n >= max_cycles:
                break
            sleep(self.settings.loop_interval_seconds)
        return n

    def stop(self) -> None:
        self.disaster_active = False

    # --------------------------------------------------------------- activation
    def evaluate_activation(self, signals: Signals):
        """Flip ``disaster_active`` based on Step 1 triggers; return the Module."""
        module = should_activate(signals)
        self.disaster_active = module is not None
        return module


def build_system(
    bus: MessageBus | None = None,
    logger: DecisionLogger | None = None,
    settings: Settings | None = None,
) -> CoordinationLoop:
    """Construct and wire the full DisasterMind agent DAG on one bus.

    Defensive by design: a module that cannot be imported/constructed is logged
    and skipped (graceful degradation, PRD Step 10) rather than aborting boot.
    """
    bus = bus or InMemoryBus()
    logger = logger or DecisionLogger.null()
    settings = settings or Settings()
    agents: list = []
    degraded: list[str] = []
    for path in MODULE_BUILD_PATHS:
        try:
            mod = importlib.import_module(path)
            built = mod.build_agents(bus, logger, settings)
            agents.extend(built)
            log.info("wired %s (%d agent(s))", path, len(built))
        except Exception:
            degraded.append(path)
            log.exception("module %s failed to load — running degraded without it", path)
    loop = CoordinationLoop(bus=bus, logger=logger, settings=settings, agents=agents)
    loop.degraded_modules = degraded
    return loop
