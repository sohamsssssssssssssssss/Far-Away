"""``LiveSystem`` — the production runner that wires real backends (PRD Step 9/10).

This is the deployment seam between the proven, in-memory orchestration DAG and
the real infrastructure described in the PRD (Kafka, PostGIS, TimescaleDB,
Elasticsearch, MinIO). It is **offline-safe by default**: with a plain
:class:`~disastermind.core.config.Settings` it builds exactly the same
deterministic in-memory system the test-suite already drives, contacting nothing.

Wiring decisions, all opt-in:

* **Bus** — :class:`~disastermind.core.bus.KafkaBus` when ``settings.use_kafka``
  is set, otherwise :class:`~disastermind.core.bus.InMemoryBus`. ``KafkaBus``
  already degrades to an in-memory fallback when no broker / ``confluent_kafka``
  is present, so the single-process coordination loop still fans messages out
  synchronously and reaches a real ``DISPATCH`` even in "Kafka" mode (PRD Step 10
  graceful degradation / auto-failover).
* **Persistence** — :class:`~disastermind.storage.Storage` built via
  ``Storage.from_settings(settings, live=...)``. ``live`` defaults to *off* (pure
  in-memory). When enabled with configured DSNs/URLs it wires real PostGIS /
  Timescale / ES / MinIO repositories — each of which still degrades to its own
  in-memory fallback if its server is unreachable, so enabling ``live`` never
  forces a network call at construction time.
* **Audit** — a durable hash-chained :class:`~disastermind.audit.decision_log.DecisionLogger`
  when ``live`` is requested (writing JSONL on disk, optionally mirrored to ES),
  otherwise the in-memory null logger used by tests.

Everything is constructed eagerly but lazily-degrading: nothing here imports an
optional client at module import time, and no method performs a blocking network
call. :meth:`run`/:meth:`run_once` simply delegate to the wired
:class:`~disastermind.orchestration.loop.CoordinationLoop`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.bus import InMemoryBus, KafkaBus, MessageBus
from ..core.config import Settings
from ..orchestration.build import CoordinationLoop, build_system

log = logging.getLogger("disastermind.live")


def select_bus(settings: Settings) -> MessageBus:
    """Pick the message bus for ``settings`` (PRD Step 10 bus selection).

    ``settings.use_kafka`` selects a :class:`KafkaBus` built from the configured
    brokers; it self-degrades to an in-memory fallback when no broker or
    ``confluent_kafka`` client is reachable, so the synchronous in-memory
    fan-out still delivers every message. Otherwise we use a plain
    :class:`InMemoryBus`. Never raises — any failure falls back to in-memory.
    """
    if not getattr(settings, "use_kafka", False):
        return InMemoryBus()
    try:
        return KafkaBus(
            brokers=getattr(settings, "kafka_brokers", "") or "",
            backup_brokers=getattr(settings, "kafka_backup_brokers", "") or None,
        )
    except Exception:  # pragma: no cover - defensive (Step 10): never block boot
        log.exception("KafkaBus construction failed — falling back to InMemoryBus")
        return InMemoryBus()


def build_logger(settings: Settings, *, live: bool) -> DecisionLogger:
    """Build the decision logger (PRD Step 9 — tamper-evident audit).

    ``live`` writes a durable, hash-chained JSONL trail (optionally mirrored to
    Elasticsearch when ``settings.elasticsearch_url`` is set); otherwise the
    in-memory null logger keeps the system fully offline for tests. Falls back to
    the null logger if a durable logger cannot be constructed.
    """
    if not live:
        return DecisionLogger.null()
    try:
        return DecisionLogger(
            path=getattr(settings, "audit_log_path", "") or "./audit.jsonl",
            elasticsearch_url=getattr(settings, "elasticsearch_url", "") or "",
        )
    except Exception:  # pragma: no cover - defensive (Step 10)
        log.exception("durable DecisionLogger failed — falling back to null logger")
        return DecisionLogger.null()


@dataclass
class LiveSystem:
    """A wired, deployable DisasterMind system.

    Holds the constructed :class:`CoordinationLoop`, the storage facade, and the
    settings used to build them. Construct via :meth:`build`; drive via
    :meth:`run`/:meth:`run_once`; observe via :meth:`health`.
    """

    loop: CoordinationLoop
    storage: Any
    settings: Settings
    live: bool = False
    live_feeds: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    # ----------------------------------------------------------------- factory
    @classmethod
    def build(
        cls,
        settings: Settings | None = None,
        *,
        live: bool = False,
        live_feeds: bool = False,
        bus: MessageBus | None = None,
        storage: Any | None = None,
    ) -> "LiveSystem":
        """Wire the full system for deployment (PRD Step 9/10).

        Parameters
        ----------
        settings:
            Runtime configuration. Defaults to a fresh :class:`Settings` read
            from the environment.
        live:
            When ``True`` (or ``settings`` requests it) attaches real backends:
            ``Storage.from_settings(live=True)`` and a durable on-disk audit log.
            Each backend still degrades to its own in-memory fallback when
            unreachable, so even ``live=True`` never forces a network call at
            build time. Defaults to ``False`` (pure in-memory / offline).
        live_feeds:
            Opt-in switch for **live Tier-3 feed polling**. When ``True``,
            :meth:`poll_live` (and the per-cycle feed poll) drives the ingestion
            adapters' real ``fetch()->parse()`` path; when ``False`` (the
            DEFAULT) feed polling uses each adapter's offline ``sample()``
            fixture. This flag never causes a network call at build time and is
            independent of ``live`` (a deployment may persist live yet still poll
            feeds offline, or vice-versa). Defaults to ``False`` so the existing
            offline runtime is unchanged and nothing hits the network.
        bus / storage:
            Pre-built overrides (mainly for tests / advanced wiring). When
            omitted they are selected from ``settings``.

        Returns a ready :class:`LiveSystem`; the underlying ``build_system`` is
        defensive, so a module that fails to load is skipped (degraded) rather
        than aborting the build.
        """
        settings = settings or Settings()
        # ``live`` is opt-in: honour an explicit flag, or a settings-driven one
        # (``DM_LIVE`` style) if a deployment sets it. Default stays offline.
        live = bool(live or getattr(settings, "live", False))
        # Live feed polling is independently opt-in (``DM_FEEDS_LIVE`` style).
        # Default OFF so feed polling stays on the offline ``sample()`` path and
        # no test/runtime cycle can reach a real socket.
        live_feeds = bool(live_feeds or getattr(settings, "live_feeds", False))

        bus = bus if bus is not None else select_bus(settings)
        logger = build_logger(settings, live=live)
        if storage is None:
            storage = _build_storage(settings, live=live)

        loop = build_system(bus=bus, logger=logger, settings=settings)

        meta: dict[str, Any] = {
            "live": live,
            "live_feeds": live_feeds,
            "bus": type(bus).__name__,
            "bus_degraded": bool(getattr(bus, "degraded", False)),
            "storage_all_fallback": _storage_all_fallback(storage),
            "degraded_modules": list(getattr(loop, "degraded_modules", []) or []),
        }
        log.info(
            "LiveSystem built (live=%s, live_feeds=%s, bus=%s, degraded_modules=%d)",
            live,
            live_feeds,
            meta["bus"],
            len(meta["degraded_modules"]),
        )
        return cls(
            loop=loop,
            storage=storage,
            settings=settings,
            live=live,
            live_feeds=live_feeds,
            meta=meta,
        )

    # ------------------------------------------------------------------- drive
    def run_once(self, now_epoch: float | None = None) -> int:
        """Advance one coordination cycle without sleeping (passthrough)."""
        return self.loop.run_once(now_epoch)

    def poll_live(self, *, live: bool | None = None, transport: Any = None) -> int:
        """Poll the Tier-3 ingestion feeds once, emitting RAW_FEED (PRD Step 2).

        Opt-in feed ingestion seam. ``live`` defaults to this system's
        ``live_feeds`` flag (set at :meth:`build` time and OFF by default): when
        effectively ``False`` every feed uses its offline ``sample()`` path (no
        network, deterministic); when ``True`` the real ``fetch()->parse()`` path
        runs. ``transport`` is an injectable ``(url, timeout) -> (status, text)``
        stub supplied **only by tests**; production leaves it ``None`` so the
        shared HTTP transport is used. Returns the number of RAW_FEED messages
        emitted. Never raises — a failing feed is skipped (graceful degradation).
        """
        from .ingest import poll_feeds

        effective_live = self.live_feeds if live is None else bool(live)
        return poll_feeds(self.loop, live=effective_live, transport=transport)

    def run(self, max_cycles: int | None = None, clock=None, sleep=None) -> int:
        """Drive the wall-clock coordination loop (PRD Step 10).

        Thin wrapper over :meth:`CoordinationLoop.run`; ``clock``/``sleep`` are
        injectable for deterministic tests. Returns cycles executed.
        """
        return self.loop.run(max_cycles=max_cycles, clock=clock, sleep=sleep)

    def stop(self) -> None:
        """Signal the coordination loop to stop after its current cycle."""
        self.loop.stop()

    # ------------------------------------------------------------------ health
    def health(self) -> dict:
        """Return an operator-facing liveness/status dict (PRD Step 9/10).

        Delegates to ``disastermind.ops`` or ``disastermind.observability.health``
        / ``disastermind.diagnostics`` when importable (they may be built
        concurrently), each behind a lazy ``try/except`` so a missing or broken
        probe never takes the health check down. Always falls back to a built-in
        report so this method *always* returns a dict and *never* raises.
        """
        report = self._delegated_health()
        if report is None:
            report = self._builtin_health()
        # Enrich with live-runtime context regardless of which probe produced it.
        report.setdefault("live", self.live)
        report.setdefault("storage", self._storage_health())
        report["bus"] = self._bus_health(report.get("bus"))
        return report

    # ----------------------------------------------------------- health: parts
    def _delegated_health(self) -> dict | None:
        """Try external health providers; return a dict or ``None`` on absence."""
        # (1) A dedicated ops package (may be built concurrently by Session B's
        # peer). Probe a few conventional entry points, all duck-typed.
        try:
            import disastermind.ops as ops  # type: ignore

            for attr in ("health", "system_health", "report_health"):
                fn = getattr(ops, attr, None)
                if callable(fn):
                    out = _call_health(fn, self)
                    if isinstance(out, dict):
                        return out
        except Exception:  # pragma: no cover - ops may be absent/half-built
            log.debug("disastermind.ops health unavailable", exc_info=True)

        # (2) The observability health probe (stable, takes the loop).
        try:
            from ..observability.health import health as obs_health

            out = obs_health(self.loop)
            if isinstance(out, dict):
                return out
        except Exception:  # pragma: no cover - defensive
            log.debug("observability.health unavailable", exc_info=True)

        # (3) The diagnostics doctor (machine-readable report).
        try:
            from ..diagnostics import run_diagnostics

            report = run_diagnostics(settings=self.settings)
            out = report.to_dict()
            if isinstance(out, dict):
                out.setdefault("status", "ok")
                return out
        except Exception:  # pragma: no cover - defensive
            log.debug("diagnostics.run_diagnostics unavailable", exc_info=True)
        return None

    def _builtin_health(self) -> dict:
        """Self-contained fallback health report (never raises)."""
        loop = self.loop
        degraded = list(getattr(loop, "degraded_modules", []) or [])
        return {
            "status": "degraded" if degraded else "ok",
            "agent_count": len(list(getattr(loop, "agents", []) or [])),
            "degraded_modules": degraded,
            "disaster_active": bool(getattr(loop, "disaster_active", False)),
            "cycle": int(getattr(loop, "cycle", 0) or 0),
        }

    def _bus_health(self, existing: Any) -> dict:
        """Normalise the bus section so callers always see type + degraded."""
        bus = getattr(self.loop, "bus", None)
        base = existing if isinstance(existing, dict) else {}
        base.setdefault("type", type(bus).__name__ if bus is not None else None)
        base["degraded"] = bool(getattr(bus, "degraded", False))
        return base

    def _storage_health(self) -> dict:
        """Summarise persistence status without contacting any backend."""
        return {
            "live": self.live,
            "all_fallback": _storage_all_fallback(self.storage),
        }


# --------------------------------------------------------------------- helpers
def _build_storage(settings: Settings, *, live: bool) -> Any:
    """Build the storage facade, defaulting to the offline in-memory path."""
    try:
        from ..storage import Storage

        return Storage.from_settings(settings, live=live)
    except Exception:  # pragma: no cover - defensive (Step 10)
        log.exception("storage build failed — continuing without persistence facade")
        return None


def _storage_all_fallback(storage: Any) -> bool:
    """True when storage is absent or every repo is in in-memory/degraded mode."""
    if storage is None:
        return True
    try:
        return bool(getattr(storage, "all_fallback", True))
    except Exception:  # pragma: no cover - defensive
        return True


def _call_health(fn, system: "LiveSystem"):
    """Invoke an external health fn, trying the loop then the system then no-arg."""
    for arg in (system.loop, system):
        try:
            return fn(arg)
        except TypeError:
            continue
        except Exception:  # pragma: no cover - provider raised; try next form
            continue
    try:
        return fn()
    except Exception:  # pragma: no cover - give up; caller falls back
        return None
