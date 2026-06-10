"""Deterministic load / throughput harness for DisasterMind (PRD Step 10).

This module drives *N* synthetic incidents through the full agent DAG and
collects throughput metrics — **without any wall-clock measurement**. PRD Step 10
("the 30-second coordination loop") requires the pipeline to absorb many
concurrent incidents while degrading gracefully; this harness exercises that path
offline and deterministically so it can run in CI as a structural regression
guard rather than a flaky benchmark.

Design choices that keep it deterministic (PRD HARD RULE 2):
  * **No real time.** "Throughput" is measured against an *injected step counter*
    (a monotone publish counter on the bus), never ``time.time``. We report
    ``messages_processed`` and ``per_cycle_messages`` — counts, not seconds.
  * **Synchronous in-memory bus.** We reuse the proven
    :func:`disastermind.scenarios.base.build_loop` wiring (subscriber-before-
    producer) and inject synthetic ``RAW_FEED`` events exactly as the scenario
    simulators do, so the load-bearing chain
    ``RAW_FEED -> PREDICTION -> ... -> DISPATCH`` fires per incident.
  * **Bounded memory.** The harness lets callers pin the bus ring-buffer cap
    (:class:`~disastermind.core.bus.InMemoryBus` ``history``) so a soak run cannot
    grow the history list without bound (PRD Step 10 resource safety).

The harness builds standalone against frozen foundation/orchestration/scenario
code and edits nothing — behaviour the storage/observability layers "could
adopt" is exposed here for adoption, not patched in.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..core.bus import InMemoryBus, MessageBus
from ..core.contracts import Message, MessageType, Module, Priority, Topic
from ..orchestration.loop import CoordinationLoop
from ..scenarios.base import (
    build_loop,
    inject_raw_event,
    inject_rescue_prediction,
    seed_field_teams,
)

# Per-module synthetic incident templates (mirror the scenario simulators but are
# parameterised so we can stamp out N distinct incidents). Each entry is the
# minimal payload the prediction tier keys off (``kind`` + ``module`` + epicentre
# + severity). Module C additionally needs rescue-priority ``risk_cells`` surfaced
# as a PREDICTION (its fire forecaster emits ``fire_fronts``, not rescue cells —
# see :mod:`disastermind.scenarios.urban_fire`).
_TEMPLATES: dict[str, dict] = {
    "A": {
        "kind": "flood",
        "module": Module.CYCLONE_FLOOD,
        "severity": 3.0,
        "meta": {
            "system_name": "Cyclone (bench)",
            "max_wind_kmph": 130.0,
            "storm_surge_m": 3.0,
            "rainfall_mm": 180.0,
            "river_level_m": 6.5,
        },
        "observations": [{"population": 1500}],
        "needs_rescue_prediction": False,
    },
    "B": {
        "kind": "earthquake",
        "module": Module.EARTHQUAKE,
        "severity": 6.2,
        "meta": {"magnitude": 6.2, "depth_km": 12.0, "tsunami": 0},
        "observations": [],
        "needs_rescue_prediction": False,
    },
    "C": {
        "kind": "urban_fire",
        "module": Module.FIRE_COLLAPSE,
        "severity": 2.6,
        "meta": {"brightness_k": 364.5, "wind_speed_ms": 16.0, "wind_dir_deg": 245.0},
        "observations": [],
        "needs_rescue_prediction": True,
    },
}

# Epicentre belt for synthetic incidents (co-located with the seeded teams so the
# field tier can bind orders to real teams and reach a DISPATCH).
_LAT, _LON = 20.30, 85.84


@dataclass
class CountingBus(MessageBus):
    """A :class:`MessageBus` decorator that counts every publish (the *step*).

    The count is the injected, deterministic notion of "work done" — we never
    consult the wall clock. It delegates publish/subscribe to a wrapped
    :class:`~disastermind.core.bus.InMemoryBus` so the synchronous fan-out (and
    its bounded ``history`` ring buffer) is exactly the production in-memory path.
    """

    inner: InMemoryBus
    published: int = 0
    #: published-count sampled at the end of each driven cycle (for deltas).
    cycle_marks: list[int] = field(default_factory=list)

    def publish(self, message: Message) -> None:  # noqa: D401 - delegates
        self.published += 1
        self.inner.publish(message)

    def subscribe(self, topic, subscriber, callback) -> None:  # noqa: D401
        self.inner.subscribe(topic, subscriber, callback)

    def close(self) -> None:  # pragma: no cover - parity with MessageBus
        self.inner.close()

    @property
    def history(self) -> list[Message]:
        return self.inner.history

    def mark_cycle(self) -> None:
        """Record the running publish count at a cycle boundary."""
        self.cycle_marks.append(self.published)


@dataclass
class BenchmarkResult:
    """Structural throughput metrics for a driven load run (JSON-serialisable).

    All fields are *counts* derived from the injected publish counter / bus
    history — never elapsed seconds — so assertions on them never flake.
    """

    incidents: int
    cycles: int
    modules: list[str]
    messages_processed: int
    dispatches: int
    escalations: int
    per_cycle_messages: list[int]
    bus_history_len: int
    bus_history_cap: int
    topic_counts: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------- injection
def _inject_incident(bus: MessageBus, key: str, n: int) -> None:
    """Inject one synthetic incident (``key`` in A/B/C) numbered ``n``."""
    tpl = _TEMPLATES[key]
    incident_id = f"bench:{key}-{n}"
    # Spread epicentres slightly so distinct incidents are not de-duplicated.
    lat = _LAT + 0.0005 * (n % 8)
    lon = _LON + 0.0005 * (n % 8)
    inject_raw_event(
        bus,
        kind=tpl["kind"],
        module=tpl["module"],
        incident_id=incident_id,
        lat=lat,
        lon=lon,
        severity=tpl["severity"],
        meta=dict(tpl["meta"]),
        observations=list(tpl["observations"]),
        priority=Priority.CRITICAL,
        reasoning=[f"synthetic {tpl['kind']} incident #{n} (benchmark harness)"],
    )
    if tpl["needs_rescue_prediction"]:
        inject_rescue_prediction(
            bus,
            module=tpl["module"],
            incident_id=incident_id,
            risk_cells=[
                {
                    "cell_id": f"{incident_id}-zone-{i}",
                    "centroid": {"lat": lat + 0.001 * i, "lon": lon + 0.001 * i},
                    "probability": 0.82,
                    "horizon_minutes": 30,
                    "population_at_risk": 450 + 60 * i,
                    "shap": {"fire_front_proximity": round(0.82 - 0.05 * i, 4)},
                }
                for i in range(3)
            ],
            reasoning=["rescue-priority zones derived from projected fire perimeter"],
        )


def _real_dispatches(history: list[Message]) -> int:
    """Count real DISPATCH orders (excluding ACK / housekeeping receipts)."""
    n = 0
    for m in history:
        if m.topic != Topic.DISPATCH:
            continue
        if m.type is MessageType.ACK:
            continue
        if (m.payload or {}).get("kind") == "dispatch_ack":
            continue
        n += 1
    return n


def _escalations(history: list[Message]) -> int:
    return sum(
        1
        for m in history
        if m.topic == Topic.ESCALATION and m.type is MessageType.ESCALATION
    )


def _topic_counts(history: list[Message]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in history:
        counts[m.topic] = counts.get(m.topic, 0) + 1
    return dict(sorted(counts.items()))


# ------------------------------------------------------------------ driver
def drive_n_incidents(
    n: int,
    cycles: int = 1,
    *,
    modules: list[str] | None = None,
    history_cap: int = 2000,
) -> BenchmarkResult:
    """Push ``n`` synthetic incidents through the DAG for ``cycles`` cycles.

    Wires the full agent DAG on a :class:`CountingBus` (subscriber-before-producer
    via :func:`disastermind.scenarios.base.build_loop`), seeds field teams, injects
    ``n`` incidents round-robin across ``modules`` (default A/B/C), then drives the
    :class:`~disastermind.orchestration.loop.CoordinationLoop` for ``cycles``
    cycles using an injected zero clock and a no-op sleep (deterministic — no real
    time, PRD Step 10).

    The publish counter is the injected "work" measure; ``per_cycle_messages`` is
    the per-cycle delta of that counter, so it is purely a function of the message
    fan-out, not the host's speed. Returns a :class:`BenchmarkResult`.
    """
    n = max(0, int(n))
    cycles = max(1, int(cycles))
    modules = [m.strip().upper() for m in (modules or ["A", "B", "C"]) if m.strip()]
    modules = [m for m in modules if m in _TEMPLATES] or ["B"]

    inner = InMemoryBus(history=history_cap)
    bus = CountingBus(inner=inner)
    loop: CoordinationLoop = build_loop(bus=bus)

    seed_field_teams(bus)
    for i in range(n):
        _inject_incident(bus, modules[i % len(modules)], i)

    # Drive each cycle explicitly so we can sample the publish counter at every
    # cycle boundary (deterministic step marks, never wall-clock).
    bus.cycle_marks.clear()
    prev = bus.published
    per_cycle: list[int] = []
    for _ in range(cycles):
        loop.run_once(now_epoch=0.0)
        cur = bus.published
        per_cycle.append(cur - prev)
        prev = cur
        bus.mark_cycle()

    history = bus.history
    return BenchmarkResult(
        incidents=n,
        cycles=cycles,
        modules=modules,
        messages_processed=bus.published,
        dispatches=_real_dispatches(history),
        escalations=_escalations(history),
        per_cycle_messages=per_cycle,
        bus_history_len=len(history),
        bus_history_cap=history_cap,
        topic_counts=_topic_counts(history),
    )
