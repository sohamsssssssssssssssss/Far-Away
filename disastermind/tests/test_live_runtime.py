"""Live-runtime wiring tests (PRD Step 9/10 deployment).

Verifies :mod:`disastermind.live` builds a deployable system that is fully
offline by default: the default build yields an in-memory system whose
``run_once`` drives the agent DAG to a real ``DISPATCH``; ``use_kafka=True``
yields a (degraded) ``KafkaBus`` that *still* reaches ``DISPATCH`` over the
in-memory fan-out; ``health()`` always returns a dict. Stdlib-only, no network.

The team-seeding + double-``run_once`` pattern mirrors the proven harness in
``tests/test_orchestration.py`` so the pipeline reaches dispatch deterministically.
"""
from __future__ import annotations

import json

from disastermind.core.bus import InMemoryBus, KafkaBus
from disastermind.core.config import Settings
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.live import LiveSystem
from disastermind.live.system import select_bus

SAMPLE_TEAMS = [
    ("BOAT-01", "boat", 20.27, 85.84),
    ("NDRF-01", "ndrf_team", 20.30, 85.82),
    ("MED-01", "medical_unit", 20.29, 85.83),
    ("HELI-01", "helicopter", 20.24, 85.81),
    ("USAR-01", "usar_team", 20.31, 85.86),
    ("FIRE-01", "fire_engine", 20.28, 85.85),
]


def _seed_teams(system: LiveSystem) -> None:
    readings = [
        {"team_id": t, "asset_type": a, "location": {"lat": la, "lon": lo}, "status": "idle"}
        for (t, a, la, lo) in SAMPLE_TEAMS
    ]
    system.loop.bus.publish(
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


def _bus_history(bus) -> list:
    """Recorded messages — for a degraded KafkaBus they live on its fallback."""
    if hasattr(bus, "history"):
        return bus.history
    fallback = getattr(bus, "_fallback", None)
    return list(getattr(fallback, "history", []) or [])


def _drive_to_dispatch(system: LiveSystem) -> set[str]:
    _seed_teams(system)
    system.run_once(now_epoch=1000.0)
    system.run_once(now_epoch=1000.0)
    return {m.topic for m in _bus_history(system.loop.bus)}


# ------------------------------------------------------------------ defaults
def test_build_default_is_offline_in_memory():
    system = LiveSystem.build()
    assert isinstance(system.loop.bus, InMemoryBus)
    assert system.live is False
    # Default persistence is the in-memory fallback — zero external services.
    assert system.storage is not None
    assert system.storage.all_fallback is True
    assert system.meta["bus"] == "InMemoryBus"
    assert system.meta["storage_all_fallback"] is True
    # The DAG wired without losing any module.
    assert system.loop.degraded_modules == []
    assert len(system.loop.agents) >= 15


def test_build_accepts_explicit_settings():
    system = LiveSystem.build(settings=Settings())
    assert isinstance(system.loop.bus, InMemoryBus)
    assert system.settings.use_kafka is False


# ------------------------------------------------------ pipeline -> dispatch
def test_run_once_drives_dag_to_real_dispatch():
    system = LiveSystem.build()
    seen = _drive_to_dispatch(system)
    for stage in (
        Topic.RAW_FEED,
        Topic.PREDICTION,
        Topic.CASCADE,
        Topic.RESOURCE_PLAN,
        Topic.ROUTING_PLAN,
        Topic.FIELD_ORDER,
        Topic.DISPATCH,
    ):
        assert stage in seen, f"stage {stage} never fired through the live loop"
    assert system.loop.cycle == 2


def test_run_once_passthrough_returns_cycle():
    system = LiveSystem.build()
    assert system.run_once(now_epoch=1000.0) == 1
    assert system.run_once(now_epoch=1000.0) == 2


# ------------------------------------------------------------- kafka degraded
def test_use_kafka_selects_degraded_kafkabus_but_still_dispatches():
    settings = Settings()
    settings.use_kafka = True  # no broker configured -> KafkaBus self-degrades
    system = LiveSystem.build(settings=settings)

    assert isinstance(system.loop.bus, KafkaBus)
    assert system.loop.bus.degraded is True  # degraded to in-memory fallback
    assert system.meta["bus"] == "KafkaBus"
    assert system.meta["bus_degraded"] is True

    seen = _drive_to_dispatch(system)
    assert Topic.DISPATCH in seen, "degraded KafkaBus must still reach DISPATCH"


def test_select_bus_honours_use_kafka_flag():
    assert isinstance(select_bus(Settings()), InMemoryBus)
    s = Settings()
    s.use_kafka = True
    assert isinstance(select_bus(s), KafkaBus)


# --------------------------------------------------------------------- health
def test_health_returns_dict():
    system = LiveSystem.build()
    report = system.health()
    assert isinstance(report, dict)
    assert "status" in report
    assert report["live"] is False
    assert isinstance(report["bus"], dict)
    assert report["bus"]["degraded"] is False
    assert isinstance(report["storage"], dict)
    assert report["storage"]["all_fallback"] is True


def test_health_reflects_kafka_degraded_bus():
    s = Settings()
    s.use_kafka = True
    system = LiveSystem.build(settings=s)
    report = system.health()
    assert isinstance(report, dict)
    assert report["bus"]["type"] == "KafkaBus"
    assert report["bus"]["degraded"] is True


def test_health_after_dispatch_is_serialisable():
    system = LiveSystem.build()
    _drive_to_dispatch(system)
    report = system.health()
    # Must be JSON-serialisable for a /healthz endpoint.
    json.dumps(report, default=str)
    assert report["cycle"] == 2


# ----------------------------------------------------------------------- run
def test_run_honours_max_cycles_without_sleeping():
    system = LiveSystem.build()
    slept: list[float] = []
    n = system.run(max_cycles=3, clock=lambda: 1000.0, sleep=lambda s: slept.append(s))
    assert n == 3
    assert len(slept) == 2  # sleeps between cycles, not after the last


def test_stop_halts_the_loop():
    system = LiveSystem.build()
    system.run(max_cycles=1, clock=lambda: 1000.0, sleep=lambda s: None)
    system.stop()
    assert system.loop.disaster_active is False


# ---------------------------------------------------------------- entrypoint
def test_main_runs_offline_and_exits_zero():
    import io

    from disastermind.live.__main__ import main

    buf = io.StringIO()
    rc = main(["--max-cycles", "1"], out=buf)
    assert rc == 0
    assert "disastermind.live: built" in buf.getvalue()
    assert "ran 1 cycle(s)" in buf.getvalue()


def test_main_health_flag_prints_json():
    import io

    from disastermind.live.__main__ import main

    buf = io.StringIO()
    rc = main(["--health"], out=buf)
    assert rc == 0
    # The JSON health block is present and parseable.
    text = buf.getvalue()
    start = text.index("{")
    json.loads(text[start:])
