"""Per-incident isolated agent DAGs and their lifecycle manager.

PRD: *"one Commander Agent per active disaster."* The :class:`IncidentManager`
spins up an isolated, fully-wired agent DAG (its own bus + its own
:class:`~disastermind.orchestration.loop.CoordinationLoop`) per ``incident_id``
and drives them concurrently in lock-step. See the package docstring for the
isolation-by-private-bus design rationale.

Everything here is built strictly against frozen modules — we *call*
:func:`disastermind.orchestration.build.build_system` and
:func:`disastermind.orchestration.triggers.should_activate`; we never edit them.
Stdlib only, offline, deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..audit.decision_log import DecisionLogger
from ..core.bus import InMemoryBus
from ..core.config import Settings
from ..core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from ..orchestration.build import build_system
from ..orchestration.loop import CoordinationLoop
from ..orchestration.triggers import Signals, should_activate

# ---------------------------------------------------------------------------
# Mapping the abstract activation Module -> the concrete RAW_FEED event kind the
# Tier 2 prediction agent keys off. This lives in OUR package (HARD RULE 3) so we
# never edit core/contracts.py or orchestration/triggers.py.
_MODULE_EVENT_KIND: dict[Module, str] = {
    Module.CYCLONE_FLOOD: "flood",
    Module.EARTHQUAKE: "earthquake",
    Module.FIRE_COLLAPSE: "urban_fire",
}

#: Default pre-positioned mixed-asset roster, co-located near a synthetic
#: epicentre, mirroring ``scenarios.base.DEFAULT_TEAMS`` / ``conftest.SAMPLE_TEAMS``.
#: GPS beacons with these ids let each incident's field coordinator bind
#: deployment orders to real teams so its chain reaches DISPATCH (PRD Step 2/6).
DEFAULT_TEAMS: list[tuple[str, str, float, float]] = [
    ("BOAT-01", "boat", 20.27, 85.84),
    ("BOAT-02", "boat", 20.35, 85.90),
    ("NDRF-01", "ndrf_team", 20.30, 85.82),
    ("NDRF-02", "ndrf_team", 20.33, 85.88),
    ("SDRF-01", "sdrf_team", 20.25, 85.88),
    ("MED-01", "medical_unit", 20.29, 85.83),
    ("MED-02", "medical_unit", 20.31, 85.86),
    ("HELI-01", "helicopter", 20.24, 85.81),
    ("USAR-01", "usar_team", 20.31, 85.86),
    ("USAR-02", "usar_team", 20.28, 85.80),
    ("FIRE-01", "fire_engine", 20.28, 85.85),
    ("FIRE-02", "fire_engine", 20.30, 85.87),
]


# ---------------------------------------------------------------------------
@dataclass
class IncidentSeed:
    """An explicit hazard description used to activate a single incident.

    Either pass an :class:`IncidentSeed` to :meth:`IncidentManager.activate`
    (you name the module + hazard directly) or pass a
    :class:`~disastermind.orchestration.triggers.Signals` snapshot and let
    :func:`~disastermind.orchestration.triggers.should_activate` pick the module
    (PRD Step 1 trigger logic). The seed carries everything needed to inject the
    RAW_FEED ALERT that drives the chain (PRD Step 1-7).
    """

    module: Module
    lat: float = 20.30
    lon: float = 85.84
    severity: float = 1.0
    kind: str | None = None  # defaults from module if None
    meta: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    # Module C's fire forecaster emits ``fire_fronts`` not rescue ``risk_cells``;
    # the resource allocator needs ``risk_cells``. When True we also surface a
    # synthetic rescue PREDICTION so the fire chain reaches DISPATCH (mirrors
    # ``scenarios.urban_fire``). Defaults to True for FIRE_COLLAPSE.
    rescue_risk_cells: list[dict[str, Any]] | None = None
    reasoning: list[str] = field(default_factory=list)

    def event_kind(self) -> str:
        return self.kind or _MODULE_EVENT_KIND.get(self.module, "earthquake")

    # -- ergonomic constructors mirroring the scenario generators -----------
    @classmethod
    def earthquake(
        cls, lat: float = 20.30, lon: float = 85.84, *, magnitude: float = 6.2,
        depth_km: float = 12.0, **meta: Any,
    ) -> "IncidentSeed":
        m = {"magnitude": magnitude, "depth_km": depth_km, **meta}
        return cls(
            module=Module.EARTHQUAKE, lat=lat, lon=lon, severity=magnitude, meta=m,
            reasoning=[f"synthetic M{magnitude:.1f} shallow earthquake (PRD Step 1, Module B)"],
        )

    @classmethod
    def flood(
        cls, lat: float = 20.30, lon: float = 85.84, *, river_level_m: float = 6.5,
        rainfall_mm: float = 180.0, population: int = 1500, **meta: Any,
    ) -> "IncidentSeed":
        m = {"river_level_m": river_level_m, "rainfall_mm": rainfall_mm,
             "warning_colour": "red", **meta}
        return cls(
            module=Module.CYCLONE_FLOOD, lat=lat, lon=lon, severity=3.0, meta=m,
            observations=[{"population": population}],
            reasoning=["synthetic IMD cyclone/flood alert (PRD Step 1, Module A)"],
        )

    @classmethod
    def urban_fire(
        cls, lat: float = 20.30, lon: float = 85.84, *, brightness_k: float = 364.5,
        wind_speed_ms: float = 16.0, **meta: Any,
    ) -> "IncidentSeed":
        m = {"brightness_k": brightness_k, "wind_speed_ms": wind_speed_ms,
             "wind_dir_deg": 245.0, **meta}
        return cls(
            module=Module.FIRE_COLLAPSE, lat=lat, lon=lon, severity=2.6, meta=m,
            reasoning=["synthetic FIRMS active-fire detection (PRD Step 1, Module C)"],
        )


# ---------------------------------------------------------------------------
@dataclass
class IncidentSnapshot:
    """Per-incident state view (PRD Step 9-style observability)."""

    incident_id: str
    module: Module
    active: bool
    cycles: int
    topic_counts: dict[str, int]
    dispatches: int
    escalations: int
    degraded_modules: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "module": self.module.value,
            "active": self.active,
            "cycles": self.cycles,
            "topic_counts": dict(self.topic_counts),
            "dispatches": self.dispatches,
            "escalations": self.escalations,
            "degraded_modules": list(self.degraded_modules),
        }


@dataclass
class MultiIncidentSnapshot:
    """Whole-board view: per-incident snapshots + an aggregate roll-up."""

    incidents: dict[str, IncidentSnapshot]
    aggregate: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "incidents": {k: v.to_dict() for k, v in self.incidents.items()},
            "aggregate": dict(self.aggregate),
        }


# ---------------------------------------------------------------------------
@dataclass
class IncidentRuntime:
    """One isolated, fully-wired agent DAG for a single disaster.

    Owns a private :class:`~disastermind.core.bus.InMemoryBus` and the
    :class:`~disastermind.orchestration.loop.CoordinationLoop` built on it. The
    module is fixed at activation (PRD: one Commander per active disaster).
    """

    incident_id: str
    module: Module
    bus: InMemoryBus
    loop: CoordinationLoop
    seed: IncidentSeed
    active: bool = True

    # ---------------------------------------------------------------- driving
    def run_once(self, now_epoch: float | None = 0.0) -> int:
        """Advance this incident's coordination loop one cycle (no sleep)."""
        return self.loop.run_once(now_epoch)

    # -------------------------------------------------------------- inspection
    def topic_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.bus.history:
            counts[m.topic] = counts.get(m.topic, 0) + 1
        return dict(sorted(counts.items()))

    def real_dispatches(self) -> list[Message]:
        """DISPATCH messages that are real orders (not delivery ACKs)."""
        out: list[Message] = []
        for m in self.bus.history:
            if m.topic != Topic.DISPATCH:
                continue
            if m.type is MessageType.ACK:
                continue
            if (m.payload or {}).get("kind") == "dispatch_ack":
                continue
            out.append(m)
        return out

    def escalations(self) -> list[Message]:
        """ESCALATION messages to the human dashboard (PRD Step 7)."""
        return [
            m
            for m in self.bus.history
            if m.topic == Topic.ESCALATION and m.type is MessageType.ESCALATION
        ]

    def snapshot(self) -> IncidentSnapshot:
        return IncidentSnapshot(
            incident_id=self.incident_id,
            module=self.module,
            active=self.active,
            cycles=self.loop.cycle,
            topic_counts=self.topic_counts(),
            dispatches=len(self.real_dispatches()),
            escalations=len(self.escalations()),
            degraded_modules=list(self.loop.degraded_modules),
        )


# ---------------------------------------------------------------------------
class IncidentManager:
    """Spin up / tear down / drive an isolated agent DAG per active disaster.

    PRD: one Commander per active disaster. Each ``activate`` builds a *separate*
    bus + DAG via :func:`build_system`, so incidents are fully isolated (see the
    package docstring). ``run_cycle`` drives every active incident once;
    ``snapshot`` rolls up per-incident state plus an aggregate.
    """

    def __init__(
        self,
        *,
        logger: DecisionLogger | None = None,
        settings: Settings | None = None,
        default_teams: list[tuple[str, str, float, float]] | None = None,
    ) -> None:
        # A null logger by default keeps tests offline/deterministic. Callers may
        # pass a real per-manager DecisionLogger; the same hash-chain instance is
        # shared by every incident DAG (a single coordinated audit trail).
        self._logger = logger or DecisionLogger.null()
        self._settings = settings or Settings()
        self._default_teams = list(default_teams or DEFAULT_TEAMS)
        self._incidents: dict[str, IncidentRuntime] = {}

    # ------------------------------------------------------------- properties
    @property
    def incident_ids(self) -> list[str]:
        return list(self._incidents.keys())

    @property
    def active_incidents(self) -> list[IncidentRuntime]:
        return [r for r in self._incidents.values() if r.active]

    def get(self, incident_id: str) -> IncidentRuntime | None:
        return self._incidents.get(incident_id)

    def __contains__(self, incident_id: object) -> bool:
        return incident_id in self._incidents

    def __len__(self) -> int:
        return len(self._incidents)

    # --------------------------------------------------------------- lifecycle
    def activate(
        self,
        incident_id: str,
        signals_or_seed: Signals | IncidentSeed,
        *,
        teams: list[tuple[str, str, float, float]] | None = None,
    ) -> IncidentRuntime:
        """Spin up a fresh isolated DAG for ``incident_id`` and inject its hazard.

        ``signals_or_seed`` is either:

        * a :class:`~disastermind.orchestration.triggers.Signals` snapshot — we
          run :func:`should_activate` (PRD Step 1) to pick the module and build a
          default :class:`IncidentSeed` for it, or
        * an :class:`IncidentSeed` — you name the module + hazard directly.

        Raises :class:`ValueError` for a duplicate id or for ``Signals`` that
        trip no activation predicate.
        """
        if incident_id in self._incidents:
            raise ValueError(f"incident {incident_id!r} is already active")

        seed = self._resolve_seed(signals_or_seed)

        # --- isolation: a brand-new bus + a freshly wired DAG (build_system) ---
        bus = InMemoryBus()
        loop = build_system(bus=bus, logger=self._logger, settings=self._settings)

        runtime = IncidentRuntime(
            incident_id=incident_id,
            module=seed.module,
            bus=bus,
            loop=loop,
            seed=seed,
            active=True,
        )

        # Seed field teams + inject the RAW_FEED hazard signal BEFORE any tick,
        # exactly how the scenarios prime the chain (PRD Step 1-7).
        self._seed_field_teams(bus, teams if teams is not None else self._default_teams)
        self._inject_hazard(bus, incident_id, seed)

        self._incidents[incident_id] = runtime
        return runtime

    def deactivate(self, incident_id: str) -> bool:
        """Tear down an incident: mark inactive, close its bus, drop it.

        Returns True if an incident was removed. Idempotent: a missing id is a
        no-op returning False.
        """
        runtime = self._incidents.pop(incident_id, None)
        if runtime is None:
            return False
        runtime.active = False
        runtime.loop.stop()
        try:
            runtime.bus.close()
        except Exception:  # pragma: no cover - close is best-effort (Step 10)
            pass
        return True

    # ------------------------------------------------------------------ driving
    def run_cycle(self, now_epoch: float | None = 0.0) -> dict[str, int]:
        """Drive every active incident's ``run_once`` exactly once.

        Returns ``{incident_id: cycle_number}``. One incident raising never stops
        the others (PRD Step 10 graceful degradation) — failures are swallowed so
        the rest of the board keeps coordinating.
        """
        results: dict[str, int] = {}
        for incident_id, runtime in list(self._incidents.items()):
            if not runtime.active:
                continue
            try:
                results[incident_id] = runtime.run_once(now_epoch)
            except Exception:  # pragma: no cover - defensive isolation
                results[incident_id] = runtime.loop.cycle
        return results

    def run_cycles(self, n: int, now_epoch: float | None = 0.0) -> None:
        """Drive ``n`` cycles across all active incidents (convenience)."""
        for _ in range(max(0, int(n))):
            self.run_cycle(now_epoch)

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> MultiIncidentSnapshot:
        """Per-incident ``{module, topic_counts, dispatches, escalations}`` plus
        an aggregate roll-up across all incidents (PRD Step 9 observability)."""
        per: dict[str, IncidentSnapshot] = {
            iid: rt.snapshot() for iid, rt in self._incidents.items()
        }

        modules: dict[str, int] = {}
        agg_topics: dict[str, int] = {}
        total_dispatches = 0
        total_escalations = 0
        for snap in per.values():
            modules[snap.module.value] = modules.get(snap.module.value, 0) + 1
            for topic, count in snap.topic_counts.items():
                agg_topics[topic] = agg_topics.get(topic, 0) + count
            total_dispatches += snap.dispatches
            total_escalations += snap.escalations

        aggregate = {
            "incident_count": len(per),
            "active_count": sum(1 for s in per.values() if s.active),
            "modules": dict(sorted(modules.items())),
            "topic_counts": dict(sorted(agg_topics.items())),
            "dispatches": total_dispatches,
            "escalations": total_escalations,
        }
        return MultiIncidentSnapshot(incidents=per, aggregate=aggregate)

    # ----------------------------------------------------------------- internals
    @staticmethod
    def _resolve_seed(signals_or_seed: Signals | IncidentSeed) -> IncidentSeed:
        """Coerce the activate() argument into an :class:`IncidentSeed`."""
        if isinstance(signals_or_seed, IncidentSeed):
            return signals_or_seed
        if isinstance(signals_or_seed, Signals):
            module = should_activate(signals_or_seed)
            if module is None:
                raise ValueError(
                    "Signals tripped no activation predicate (PRD Step 1); "
                    "nothing to activate"
                )
            return _seed_from_signals(module, signals_or_seed)
        raise TypeError(
            "activate() expects a Signals snapshot or an IncidentSeed, "
            f"got {type(signals_or_seed).__name__}"
        )

    @staticmethod
    def _seed_field_teams(
        bus: InMemoryBus, teams: Iterable[tuple[str, str, float, float]]
    ) -> None:
        """Publish a GPS-beacon telemetry frame (mirrors scenarios/conftest)."""
        readings = [
            {
                "team_id": tid,
                "asset_type": atype,
                "location": {"lat": lat, "lon": lon},
                "status": "idle",
            }
            for (tid, atype, lat, lon) in teams
        ]
        bus.publish(
            Message(
                sender="iot.gps_beacon",
                recipient="broadcast",
                type=MessageType.QUERY,
                priority=Priority.INFO,
                topic=Topic.IOT_TELEMETRY,
                module=Module.ALL,
                payload={"kind": "gps_beacon", "readings": readings},
            )
        )

    @staticmethod
    def _inject_hazard(bus: InMemoryBus, incident_id: str, seed: IncidentSeed) -> None:
        """Inject the RAW_FEED ALERT (and, for fire, a rescue PREDICTION).

        Same envelope a Tier 3 feed adapter emits: ``payload["event"]`` is a
        JSON-able DisasterEvent the prediction tier keys off (PRD Step 1-7).
        """
        kind = seed.event_kind()
        event = {
            "incident_id": incident_id,
            "kind": kind,
            "epicentre": {"lat": float(seed.lat), "lon": float(seed.lon)},
            "severity": float(seed.severity),
            "detected_at": "2026-06-08T00:00:00+00:00",
            "source": "multi_incident_manager",
            "meta": dict(seed.meta),
        }
        bus.publish(
            Message(
                sender="ingest.multi_incident",
                recipient="tier2.prediction",
                type=MessageType.ALERT,
                priority=Priority.CRITICAL,
                payload={
                    "kind": "scenario_signal",
                    "event": event,
                    "observations": list(seed.observations),
                },
                reasoning=seed.reasoning or [f"synthetic {kind} signal (multi-incident)"],
                topic=Topic.RAW_FEED,
                module=seed.module,
                incident_id=incident_id,
            )
        )

        # Module C: the fire forecaster emits fire_fronts, but the resource
        # allocator builds demand from rescue risk_cells — surface them so the
        # fire chain reaches DISPATCH (mirrors scenarios.urban_fire, PRD Step 4-7).
        if seed.module is Module.FIRE_COLLAPSE:
            cells = seed.rescue_risk_cells
            if cells is None:
                cells = _default_fire_rescue_zones(seed.lat, seed.lon)
            if cells:
                bus.publish(
                    Message(
                        sender="tier2.prediction.multi_incident",
                        recipient="tier2.cascade",
                        type=MessageType.ALERT,
                        priority=Priority.CRITICAL,
                        payload={
                            "kind": "risk",
                            "incident_id": incident_id,
                            "module": Module.FIRE_COLLAPSE.value,
                            "risk_cells": list(cells),
                            "buildings": [],
                            "fire_fronts": [],
                        },
                        reasoning=[
                            "rescue-priority zones from projected fire perimeter "
                            "(multi-incident)"
                        ],
                        topic=Topic.PREDICTION,
                        incident_id=incident_id,
                        module=Module.FIRE_COLLAPSE,
                    )
                )


# ---------------------------------------------------------------------------
def _seed_from_signals(module: Module, signals: Signals) -> IncidentSeed:
    """Build a default :class:`IncidentSeed` from an activated :class:`Signals`.

    Carries the trigger-relevant fields into the seed's ``meta`` so the injected
    RAW_FEED event reflects the real signal that fired (PRD Step 1).
    """
    if module is Module.EARTHQUAKE:
        return IncidentSeed.earthquake(magnitude=max(4.5, signals.max_seismic_magnitude))
    if module is Module.CYCLONE_FLOOD:
        return IncidentSeed.flood(
            river_level_m=signals.river_gauge_pct_of_danger / 100.0 * 5.0,
            imd_cyclone_alert=signals.imd_cyclone_alert,
            waterlogging_breach_zones=signals.waterlogging_breach_zones,
        )
    if module is Module.FIRE_COLLAPSE:
        return IncidentSeed.urban_fire(
            brigade_calls=signals.fire_brigade_calls_in_zone_10min,
            firms_thermal_anomaly=signals.firms_thermal_anomaly,
        )
    # Defensive: should_activate only ever returns the three above.
    return IncidentSeed(module=module)  # pragma: no cover


def _default_fire_rescue_zones(lat: float, lon: float) -> list[dict[str, Any]]:
    """Rescue-priority risk cells around a fire front (mirrors scenarios)."""
    zones: list[dict[str, Any]] = []
    for i in range(4):
        zones.append(
            {
                "cell_id": f"fire-rescue-zone-{i}",
                "centroid": {"lat": lat + 0.001 * i, "lon": lon + 0.001 * i},
                "probability": 0.82,
                "horizon_minutes": 30,
                "population_at_risk": 450 + 60 * i,
                "shap": {"fire_front_proximity": round(0.82 - 0.05 * i, 4)},
            }
        )
    return zones
