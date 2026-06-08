"""Orchestration layer: Step 1 triggers + Step 10 coordination loop.

Covers the parts wired by ``disastermind.orchestration`` (build_system /
CoordinationLoop / should_activate), complementing the conftest-wired e2e.
"""
from __future__ import annotations

from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.orchestration.build import (
    Signals,
    activation_report,
    build_system,
    should_activate,
)

SAMPLE_TEAMS = [
    ("BOAT-01", "boat", 20.27, 85.84),
    ("NDRF-01", "ndrf_team", 20.30, 85.82),
    ("MED-01", "medical_unit", 20.29, 85.83),
    ("HELI-01", "helicopter", 20.24, 85.81),
    ("USAR-01", "usar_team", 20.31, 85.86),
    ("FIRE-01", "fire_engine", 20.28, 85.85),
]


def _seed_teams(loop):
    readings = [
        {"team_id": t, "asset_type": a, "location": {"lat": la, "lon": lo}, "status": "idle"}
        for (t, a, la, lo) in SAMPLE_TEAMS
    ]
    loop.bus.publish(
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


# --------------------------------------------------------------- triggers
def test_should_activate_per_module():
    assert should_activate(Signals(max_seismic_magnitude=6.2)) is Module.EARTHQUAKE
    assert should_activate(Signals(max_seismic_magnitude=4.4)) is None
    assert should_activate(Signals(river_gauge_pct_of_danger=80)) is Module.CYCLONE_FLOOD
    assert should_activate(Signals(waterlogging_breach_zones=3)) is Module.CYCLONE_FLOOD
    assert should_activate(Signals(waterlogging_breach_zones=2)) is None
    assert should_activate(Signals(fire_brigade_calls_in_zone_10min=3)) is Module.FIRE_COLLAPSE
    assert should_activate(Signals(firms_thermal_anomaly=True)) is Module.FIRE_COLLAPSE
    assert should_activate(Signals()) is None


def test_seismic_takes_precedence_when_cooccurring():
    s = Signals(max_seismic_magnitude=5.0, river_gauge_pct_of_danger=90)
    assert should_activate(s) is Module.EARTHQUAKE  # 90s > 72h criticality
    modules = {d.module for d in activation_report(s)}
    assert {Module.EARTHQUAKE, Module.CYCLONE_FLOOD} <= modules


# --------------------------------------------------------------- bootstrap
def test_build_system_wires_all_modules_without_degradation():
    loop = build_system()
    assert loop.degraded_modules == []
    assert len(loop.agents) >= 15
    assert loop.commander is not None
    assert loop.disaster_active is False


def test_run_once_drives_full_pipeline_to_dispatch():
    loop = build_system()
    _seed_teams(loop)
    loop.run_once(now_epoch=1000.0)
    loop.run_once(now_epoch=1000.0)

    seen = {m.topic for m in loop.bus.history}
    for stage in (
        Topic.RAW_FEED,
        Topic.PREDICTION,
        Topic.CASCADE,
        Topic.RESOURCE_PLAN,
        Topic.ROUTING_PLAN,
        Topic.FIELD_ORDER,
        Topic.DISPATCH,
    ):
        assert stage in seen, f"stage {stage} never fired through the loop"
    assert loop.cycle == 2


def test_run_loop_honours_max_cycles_without_sleeping():
    loop = build_system()
    slept: list[float] = []
    n = loop.run(max_cycles=3, clock=lambda: 1000.0, sleep=lambda s: slept.append(s))
    assert n == 3
    # sleeps happen between cycles, not after the last one
    assert len(slept) == 2
    assert all(s == loop.settings.loop_interval_seconds for s in slept)


def test_evaluate_activation_flips_disaster_active():
    loop = build_system()
    assert loop.evaluate_activation(Signals()) is None
    assert loop.disaster_active is False
    assert loop.evaluate_activation(Signals(max_seismic_magnitude=5.5)) is Module.EARTHQUAKE
    assert loop.disaster_active is True
