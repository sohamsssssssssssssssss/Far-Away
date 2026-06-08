"""The individual doctor probes.

Each ``check_*`` function appends one or more :class:`Check` results to a
:class:`Report`. They are deliberately pure-ish (no global state, no network)
and never raise on a defect — a defect becomes a ``FAIL`` check so a single
broken probe never aborts the whole diagnosis (PRD Step 10 graceful degradation,
applied to our own self-check).

What we verify
--------------
a. **Modules** — every path in ``orchestration.MODULE_BUILD_PATHS`` imports and
   its ``build_agents(bus, logger, settings)`` constructs at least one agent.
b. **Topic DAG balance** — from a dry ``build_system`` + one seeded ``run_once``
   we build a *produces-map* (topics actually published) and read the bus
   *subscriptions*. Every produced topic must have a subscriber (except declared
   terminal sinks); no subscriber may wait on a topic nobody could ever produce
   (i.e. not produced AND not a declared well-known ``Topic`` contract).
c. **Config sanity** — loop interval > 0, escalation timeout > 0, DSNs present.
d. **Audit chain** — ``DecisionLogger(path).verify_chain()`` on a given path.
e. **Backends (optional)** — ``disastermind.integrations.health`` if importable.
"""
from __future__ import annotations

import importlib
import os
from typing import Any

from .report import Report, Status

# Topics that are intentional *terminal sinks*: produced for an out-of-band
# consumer (e.g. the human dashboard reads the escalation narrative directly) so
# having no in-bus subscriber is by-design, NOT an orphan producer.
TERMINAL_SINK_TOPICS: frozenset[str] = frozenset({"tier1.escalation_narrative"})
#: any produced topic whose name ends with one of these is also a terminal sink.
TERMINAL_SINK_SUFFIXES: tuple[str, ...] = ("_narrative",)


def _is_terminal_sink(topic: str) -> bool:
    return topic in TERMINAL_SINK_TOPICS or topic.endswith(TERMINAL_SINK_SUFFIXES)


# --------------------------------------------------------------------------- a
def check_modules(report: Report, settings) -> dict[str, Any]:
    """Probe each MODULE_BUILD_PATHS entry: import + build_agents constructs.

    Returns the wired environment ({bus, logger, agents}) so the DAG probe can
    reuse it without paying for a second build, or ``{}`` if bootstrap failed.
    """
    from ..audit.decision_log import DecisionLogger
    from ..core.bus import InMemoryBus

    try:
        from ..orchestration.build import MODULE_BUILD_PATHS
    except Exception as exc:  # pragma: no cover - orchestration is frozen/present
        report.add(
            "modules.import_paths",
            Status.FAIL,
            f"cannot import orchestration.MODULE_BUILD_PATHS: {exc!r}",
        )
        return {}

    bus = InMemoryBus()
    logger = DecisionLogger.null()
    agents: list = []
    degraded: list[str] = []

    for path in MODULE_BUILD_PATHS:
        try:
            mod = importlib.import_module(path)
        except Exception as exc:
            degraded.append(path)
            report.add(
                f"module.import:{path}",
                Status.FAIL,
                f"import failed: {exc!r}",
                {"path": path},
            )
            continue
        build_fn = getattr(mod, "build_agents", None)
        if build_fn is None:
            degraded.append(path)
            report.add(
                f"module.build_agents:{path}",
                Status.FAIL,
                "module exposes no build_agents(bus, logger, settings)",
                {"path": path},
            )
            continue
        try:
            built = build_fn(bus, logger, settings) or []
        except Exception as exc:
            degraded.append(path)
            report.add(
                f"module.build:{path}",
                Status.FAIL,
                f"build_agents raised: {exc!r}",
                {"path": path},
            )
            continue
        if not built:
            # An empty module is suspicious but not necessarily fatal.
            report.add(
                f"module.build:{path}",
                Status.WARN,
                "build_agents constructed 0 agents",
                {"path": path, "agents": 0},
            )
        else:
            agents.extend(built)
            report.add(
                f"module.build:{path}",
                Status.OK,
                f"{len(built)} agent(s) constructed",
                {"path": path, "agents": len(built)},
            )

    report.meta["modules_total"] = len(MODULE_BUILD_PATHS)
    report.meta["modules_degraded"] = degraded
    report.meta["agents_constructed"] = len(agents)

    if degraded and len(degraded) == len(MODULE_BUILD_PATHS):
        report.add(
            "modules.bootstrap",
            Status.FAIL,
            "no module could be wired — system cannot boot",
        )
        return {}

    return {"bus": bus, "logger": logger, "settings": settings, "agents": agents}


# --------------------------------------------------------------------------- b
def _seed_and_run(loop) -> None:
    """Drive one seeded cycle so producers actually publish (best-effort).

    Reuses the scenarios helpers (offline, deterministic) to push a RAW_FEED
    earthquake signal and an escalation order through the DAG so the produces-map
    is populated. Any failure here is swallowed — the DAG probe degrades to
    "subscriptions only" and reports a WARN rather than crashing.
    """
    from ..core.contracts import EscalationTrigger, Module
    from ..scenarios.base import (
        inject_escalation_order,
        inject_raw_event,
        seed_field_teams,
    )

    bus = loop.bus
    seed_field_teams(bus)
    inject_raw_event(
        bus,
        kind="earthquake",
        module=Module.EARTHQUAKE,
        incident_id="diagnostics:dry-run",
        lat=20.30,
        lon=85.84,
        severity=6.2,
        meta={"magnitude": 6.2, "depth_km": 12.0, "place": "diagnostics dry-run"},
    )
    # Force the escalation edge so tier1.escalation gets exercised too.
    inject_escalation_order(
        bus,
        module=Module.EARTHQUAKE,
        incident_id="diagnostics:dry-run",
        trigger=EscalationTrigger.CROSS_STATE_RESOURCE,
        team_id="NDRF-DIAG",
        site="diagnostics dry-run site",
        reason="diagnostics dry-run escalation edge",
        summary="diagnostics dry-run",
        scale=1,
    )
    loop.run(max_cycles=1, clock=lambda: 0.0, sleep=lambda _s: None)


def subscribed_topics(bus) -> set[str]:
    """Topics that have >=1 subscriber on an InMemoryBus (defensive)."""
    subs = getattr(bus, "_subs", None)
    if subs is None:
        return set()
    return {topic for topic, lst in subs.items() if lst}


def produced_topics(bus) -> set[str]:
    """Topics that were actually published (from the bus history)."""
    return {m.topic for m in getattr(bus, "history", [])}


def known_contract_topics() -> set[str]:
    """All well-known ``Topic`` constants (the declared contract surface)."""
    from ..core.contracts import Topic

    return {
        v
        for k, v in vars(Topic).items()
        if not k.startswith("_") and isinstance(v, str)
    }


def analyse_dag(produced: set[str], subscribed: set[str]) -> dict[str, list[str]]:
    """Pure DAG-balance analysis (unit-testable without a bus).

    A topic is *producible* if it was produced in the dry run OR it is a declared
    well-known contract topic (it can be produced by some flow we didn't exercise).

      * orphan_producers — produced, no subscriber, and not a terminal sink.
      * dead_subscribers — subscribed, but the topic is not producible at all
                           (nobody produces it and it is not a contract topic).
    """
    contract = known_contract_topics()
    producible = produced | contract

    orphan_producers = sorted(
        t for t in produced if t not in subscribed and not _is_terminal_sink(t)
    )
    dead_subscribers = sorted(t for t in subscribed if t not in producible)
    return {
        "orphan_producers": orphan_producers,
        "dead_subscribers": dead_subscribers,
    }


def check_dag(report: Report, env: dict[str, Any]) -> None:
    """Verify the topic DAG is balanced (produced<->subscribed)."""
    from ..audit.decision_log import DecisionLogger
    from ..core.bus import InMemoryBus
    from ..orchestration.loop import CoordinationLoop

    # Build a *fresh* loop for the dry run (a clean bus/history) using the same
    # already-imported modules. Reuse env agents only for the count.
    try:
        from ..orchestration.build import build_system

        loop = build_system(
            bus=InMemoryBus(),
            logger=DecisionLogger.null(),
            settings=env.get("settings"),
        )
    except Exception as exc:
        report.add(
            "dag.build",
            Status.FAIL,
            f"dry build_system failed: {exc!r}",
        )
        return

    if not isinstance(loop, CoordinationLoop):  # pragma: no cover - defensive
        report.add("dag.build", Status.FAIL, "build_system did not return a CoordinationLoop")
        return

    bus = loop.bus
    subscribed = subscribed_topics(bus)

    ran = True
    try:
        _seed_and_run(loop)
    except Exception as exc:  # pragma: no cover - scenarios are frozen/present
        ran = False
        report.add(
            "dag.dry_run",
            Status.WARN,
            f"could not drive a seeded cycle ({exc!r}); analysing subscriptions only",
        )

    produced = produced_topics(bus)
    analysis = analyse_dag(produced, subscribed)

    report.meta["dag_produced"] = sorted(produced)
    report.meta["dag_subscribed"] = sorted(subscribed)
    report.meta["dag_cycles"] = getattr(loop, "cycle", 0)

    orphans = analysis["orphan_producers"]
    deads = analysis["dead_subscribers"]

    if orphans:
        report.add(
            "dag.orphan_producers",
            Status.FAIL,
            "produced topic(s) with no subscriber: " + ", ".join(orphans),
            {"topics": orphans},
        )
    else:
        report.add(
            "dag.orphan_producers",
            Status.OK,
            "every produced topic has at least one subscriber",
        )

    if deads:
        report.add(
            "dag.dead_subscribers",
            Status.FAIL,
            "subscriber(s) waiting on a topic nobody produces: " + ", ".join(deads),
            {"topics": deads},
        )
    else:
        report.add(
            "dag.dead_subscribers",
            Status.OK,
            "no subscriber waits on an unproduced/undeclared topic",
        )

    if ran and not produced:
        report.add(
            "dag.dry_run",
            Status.WARN,
            "seeded cycle produced no messages (DAG may be inert)",
        )
    elif ran:
        report.add(
            "dag.dry_run",
            Status.OK,
            f"seeded cycle produced {len(produced)} topic(s)",
        )


# --------------------------------------------------------------------------- c
def check_config(report: Report, settings) -> None:
    """Config sanity: positive intervals/timeouts, DSNs present."""
    interval = getattr(settings, "loop_interval_seconds", 0)
    if isinstance(interval, (int, float)) and interval > 0:
        report.add("config.loop_interval", Status.OK, f"{interval}s")
    else:
        report.add(
            "config.loop_interval",
            Status.FAIL,
            f"loop_interval_seconds must be > 0 (got {interval!r})",
        )

    timeout = getattr(settings, "escalation_timeout_seconds", 0)
    if isinstance(timeout, (int, float)) and timeout > 0:
        report.add("config.escalation_timeout", Status.OK, f"{timeout}s")
    else:
        report.add(
            "config.escalation_timeout",
            Status.FAIL,
            f"escalation_timeout_seconds must be > 0 (got {timeout!r})",
        )

    grid = getattr(settings, "grid_cell_meters", None)
    if grid is None or (isinstance(grid, (int, float)) and grid > 0):
        if grid is not None:
            report.add("config.grid_cell_meters", Status.OK, f"{grid}m")
    else:
        report.add(
            "config.grid_cell_meters",
            Status.WARN,
            f"grid_cell_meters should be > 0 (got {grid!r})",
        )

    # DSNs: present (non-empty) is required; reachability is the optional probe (e).
    for attr, label in (
        ("postgres_dsn", "postgres"),
        ("timescale_dsn", "timescale"),
    ):
        dsn = getattr(settings, attr, "")
        if dsn:
            report.add(f"config.dsn.{label}", Status.OK, "present")
        else:
            report.add(
                f"config.dsn.{label}",
                Status.FAIL,
                f"{attr} is empty — no storage DSN configured",
            )

    # The audit path is informational (it may not exist yet on a fresh install).
    audit = getattr(settings, "audit_log_path", "")
    report.add(
        "config.audit_path",
        Status.OK if audit else Status.WARN,
        audit or "audit_log_path is empty",
    )


# --------------------------------------------------------------------------- d
def check_audit(report: Report, audit_path: str | None) -> None:
    """Verify the tamper-evident audit hash-chain at ``audit_path`` if given."""
    if not audit_path:
        report.add("audit.chain", Status.SKIP, "no audit_path supplied")
        return
    if not os.path.exists(audit_path):
        report.add(
            "audit.chain",
            Status.WARN,
            f"audit log not found at {audit_path}",
            {"path": audit_path},
        )
        return
    try:
        from ..audit.decision_log import DecisionLogger

        logger = DecisionLogger(path=audit_path)
        ok = logger.verify_chain()
    except Exception as exc:
        report.add(
            "audit.chain",
            Status.FAIL,
            f"verify_chain raised: {exc!r}",
            {"path": audit_path},
        )
        return
    if ok:
        report.add("audit.chain", Status.OK, f"hash-chain intact ({audit_path})")
    else:
        report.add(
            "audit.chain",
            Status.FAIL,
            f"hash-chain TAMPERED or broken ({audit_path})",
            {"path": audit_path},
        )


# --------------------------------------------------------------------------- e
def check_backends(report: Report, settings) -> None:
    """OPTIONAL backend reachability via ``disastermind.integrations.health``.

    Lazy + guarded: the integrations health module may not exist (it is owned by
    another session), and even when present its probes must never touch the
    network in a test path. We import it inside try/except and treat any
    absence/failure as a neutral SKIP — never a FAIL.
    """
    try:
        health = importlib.import_module("disastermind.integrations.health")
    except Exception:
        report.add(
            "backends.reachability",
            Status.SKIP,
            "disastermind.integrations.health not importable — skipping backend probes",
        )
        return

    probe = (
        getattr(health, "check_all", None)
        or getattr(health, "run_health_checks", None)
        or getattr(health, "health_check", None)
        or getattr(health, "check", None)
    )
    if probe is None:
        report.add(
            "backends.reachability",
            Status.SKIP,
            "integrations.health exposes no recognised probe entry point",
        )
        return

    try:
        results = _call_health_probe(probe, settings)
    except Exception as exc:
        # Reachability is best-effort; a raising probe is a WARN, not a FAIL.
        report.add(
            "backends.reachability",
            Status.WARN,
            f"integrations.health probe raised: {exc!r}",
        )
        return

    _record_backend_results(report, results)


def _call_health_probe(probe, settings):
    """Call the health probe tolerating (settings) or () signatures."""
    try:
        return probe(settings)
    except TypeError:
        return probe()


def _record_backend_results(report: Report, results: Any) -> None:
    """Normalise a variety of health-probe return shapes into checks (no FAIL).

    Optional backends being down is a degraded-but-operational condition
    (PRD Step 10), so we map unreachable -> WARN, reachable -> OK.
    """
    items: list[tuple[str, bool, str]] = []
    if isinstance(results, dict):
        for name, val in results.items():
            ok, detail = _coerce_health_value(val)
            items.append((str(name), ok, detail))
    elif isinstance(results, (list, tuple, set)):
        for entry in results:
            name, ok, detail = _coerce_health_entry(entry)
            items.append((name, ok, detail))
    else:
        ok, detail = _coerce_health_value(results)
        items.append(("integrations.health", ok, detail))

    if not items:
        report.add("backends.reachability", Status.SKIP, "health probe returned nothing")
        return

    for name, ok, detail in items:
        report.add(
            f"backend.{name}",
            Status.OK if ok else Status.WARN,
            detail or ("reachable" if ok else "unreachable"),
        )


def _coerce_health_value(val: Any) -> tuple[bool, str]:
    if isinstance(val, bool):
        return val, ""
    if isinstance(val, dict):
        ok = bool(val.get("ok", val.get("healthy", val.get("reachable", False))))
        return ok, str(val.get("detail", val.get("error", "")))
    if isinstance(val, str):
        return val.lower() in {"ok", "up", "healthy", "reachable", "true"}, val
    return bool(val), ""


def _coerce_health_entry(entry: Any) -> tuple[str, bool, str]:
    if isinstance(entry, dict):
        name = str(entry.get("name", entry.get("backend", "backend")))
        ok = bool(entry.get("ok", entry.get("healthy", entry.get("reachable", False))))
        return name, ok, str(entry.get("detail", entry.get("error", "")))
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        ok, detail = _coerce_health_value(entry[1])
        return str(entry[0]), ok, detail
    ok, detail = _coerce_health_value(entry)
    return str(entry), ok, detail
