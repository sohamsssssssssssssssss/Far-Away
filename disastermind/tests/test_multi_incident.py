"""Tests for :mod:`disastermind.multi_incident` — concurrent multi-incident
orchestration (PRD: *one Commander Agent per active disaster*).

These exercise the package's contract end-to-end with the standard library only
(no network, no broker, no solver/ML deps): activate several isolated agent DAGs
concurrently, drive them in lock-step, and assert each independently reaches a
DISPATCH while ``snapshot()`` aggregates the whole board. Every incident gets its
own private :class:`~disastermind.core.bus.InMemoryBus` (the documented
isolation-by-private-bus design), so we also assert structural isolation: one
incident's bus never carries another incident's id.
"""
from __future__ import annotations

import pytest

from disastermind.core.bus import InMemoryBus
from disastermind.core.contracts import Module, Topic
from disastermind.multi_incident import (
    IncidentManager,
    IncidentRuntime,
    IncidentSeed,
    IncidentSnapshot,
    MultiIncidentSnapshot,
)
from disastermind.orchestration.triggers import Signals


# --------------------------------------------------------------------------- API
def test_public_api_exports():
    """The package exports its documented public surface."""
    import disastermind.multi_incident as mi

    for name in (
        "IncidentManager",
        "IncidentRuntime",
        "IncidentSeed",
        "IncidentSnapshot",
        "MultiIncidentSnapshot",
    ):
        assert name in mi.__all__
        assert hasattr(mi, name)


def test_import_has_no_network_side_effects():
    """Importing the package and constructing a manager touches no network."""
    mgr = IncidentManager()
    assert len(mgr) == 0
    assert mgr.incident_ids == []
    assert mgr.active_incidents == []


# ------------------------------------------------------------------------- seeds
def test_incident_seed_constructors_pick_the_right_module():
    assert IncidentSeed.earthquake(magnitude=6.2).module is Module.EARTHQUAKE
    assert IncidentSeed.flood(river_level_m=6.5).module is Module.CYCLONE_FLOOD
    assert IncidentSeed.urban_fire().module is Module.FIRE_COLLAPSE


def test_incident_seed_event_kind_defaults_from_module():
    assert IncidentSeed.earthquake().event_kind() == "earthquake"
    assert IncidentSeed.flood().event_kind() == "flood"
    assert IncidentSeed.urban_fire().event_kind() == "urban_fire"
    # explicit kind wins
    assert IncidentSeed(module=Module.EARTHQUAKE, kind="custom").event_kind() == "custom"


# ---------------------------------------------------------------- core scenario
def test_earthquake_and_flood_concurrently_each_reach_dispatch():
    """The headline requirement: activate an earthquake AND a flood at the same
    time, run cycles, and assert each independently reaches a real DISPATCH and
    that snapshot() aggregates both."""
    mgr = IncidentManager()
    eq = mgr.activate("eq-cuttack", IncidentSeed.earthquake(20.30, 85.84, magnitude=6.2))
    fl = mgr.activate("flood-mahanadi", IncidentSeed.flood(20.30, 85.84, river_level_m=6.5))

    assert eq.module is Module.EARTHQUAKE
    assert fl.module is Module.CYCLONE_FLOOD
    assert len(mgr) == 2
    assert set(mgr.incident_ids) == {"eq-cuttack", "flood-mahanadi"}

    # Drive every active incident in lock-step.
    results = mgr.run_cycle()
    assert set(results) == {"eq-cuttack", "flood-mahanadi"}
    mgr.run_cycles(2)

    # --- each incident independently reaches a real (non-ACK) DISPATCH ---------
    assert len(eq.real_dispatches()) > 0, "earthquake chain never dispatched"
    assert len(fl.real_dispatches()) > 0, "flood chain never dispatched"

    # --- snapshot() aggregates both ------------------------------------------
    snap = mgr.snapshot()
    assert isinstance(snap, MultiIncidentSnapshot)
    assert set(snap.incidents) == {"eq-cuttack", "flood-mahanadi"}

    eq_snap = snap.incidents["eq-cuttack"]
    fl_snap = snap.incidents["flood-mahanadi"]
    assert isinstance(eq_snap, IncidentSnapshot)
    assert eq_snap.module is Module.EARTHQUAKE
    assert fl_snap.module is Module.CYCLONE_FLOOD
    assert eq_snap.dispatches > 0
    assert fl_snap.dispatches > 0

    agg = snap.aggregate
    assert agg["incident_count"] == 2
    assert agg["active_count"] == 2
    assert agg["modules"] == {"A": 1, "B": 1}
    # aggregate dispatch count is the sum of the per-incident counts
    assert agg["dispatches"] == eq_snap.dispatches + fl_snap.dispatches
    # the DISPATCH topic shows up in the aggregate topic roll-up
    assert agg["topic_counts"].get(Topic.DISPATCH, 0) > 0


def test_all_three_modules_reach_dispatch_concurrently():
    """Earthquake + flood + urban fire all coordinated at once (the fire chain
    needs its rescue-prediction shim to reach DISPATCH)."""
    mgr = IncidentManager()
    mgr.activate("eq", IncidentSeed.earthquake(magnitude=6.2))
    mgr.activate("flood", IncidentSeed.flood())
    mgr.activate("fire", IncidentSeed.urban_fire())

    mgr.run_cycles(3)
    snap = mgr.snapshot()

    assert snap.aggregate["incident_count"] == 3
    assert snap.aggregate["modules"] == {"A": 1, "B": 1, "C": 1}
    for iid in ("eq", "flood", "fire"):
        assert snap.incidents[iid].dispatches > 0, f"{iid} never dispatched"


# ------------------------------------------------------------------- isolation
def test_incidents_use_separate_buses_with_no_cross_talk():
    """Documented design: one private bus per incident => zero cross-talk. One
    incident's bus must never carry another incident's id."""
    mgr = IncidentManager()
    eq = mgr.activate("eq-X", IncidentSeed.earthquake(magnitude=6.0))
    fl = mgr.activate("flood-Y", IncidentSeed.flood())
    mgr.run_cycles(2)

    assert isinstance(eq.bus, InMemoryBus)
    assert isinstance(fl.bus, InMemoryBus)
    assert eq.bus is not fl.bus

    eq_ids = {m.incident_id for m in eq.bus.history if m.incident_id}
    fl_ids = {m.incident_id for m in fl.bus.history if m.incident_id}
    assert "eq-X" in eq_ids
    assert "flood-Y" in fl_ids
    # critical isolation invariant: no leakage of the other incident's id
    assert "flood-Y" not in eq_ids
    assert "eq-X" not in fl_ids


def test_one_failing_incident_does_not_stop_the_others():
    """PRD Step 10 graceful degradation: a raising incident never blocks the
    rest of the board's run_cycle."""
    mgr = IncidentManager()
    good = mgr.activate("good", IncidentSeed.earthquake(magnitude=6.2))
    bad = mgr.activate("bad", IncidentSeed.flood())

    # Sabotage the bad incident's loop so run_once raises.
    def boom(now_epoch=0.0):  # noqa: ARG001
        raise RuntimeError("simulated agent DAG failure")

    bad.loop.run_once = boom  # type: ignore[method-assign]

    # Should not raise; the good incident still advances.
    results = mgr.run_cycle()
    assert "good" in results and "bad" in results
    assert good.snapshot().dispatches > 0


# ------------------------------------------------------------------- lifecycle
def test_duplicate_activation_raises():
    mgr = IncidentManager()
    mgr.activate("dup", IncidentSeed.earthquake())
    with pytest.raises(ValueError):
        mgr.activate("dup", IncidentSeed.flood())


def test_deactivate_removes_incident_and_is_idempotent():
    mgr = IncidentManager()
    mgr.activate("eq", IncidentSeed.earthquake())
    mgr.activate("flood", IncidentSeed.flood())
    assert len(mgr) == 2

    assert mgr.deactivate("eq") is True
    assert "eq" not in mgr
    assert mgr.incident_ids == ["flood"]

    # idempotent: a second deactivate / missing id is a no-op returning False
    assert mgr.deactivate("eq") is False
    assert mgr.deactivate("never-existed") is False


def test_run_cycle_skips_nothing_after_deactivate():
    mgr = IncidentManager()
    mgr.activate("eq", IncidentSeed.earthquake())
    mgr.activate("flood", IncidentSeed.flood())
    mgr.deactivate("flood")
    results = mgr.run_cycle()
    assert set(results) == {"eq"}
    snap = mgr.snapshot()
    assert snap.aggregate["incident_count"] == 1
    assert snap.aggregate["active_count"] == 1


# ----------------------------------------------------- Signals-driven activation
def test_activate_from_signals_picks_module_via_should_activate():
    """Passing a Signals snapshot lets should_activate (PRD Step 1) pick the
    module; the resulting DAG still reaches DISPATCH."""
    mgr = IncidentManager()
    rt_eq = mgr.activate("sig-eq", Signals(max_seismic_magnitude=6.0))
    rt_fl = mgr.activate(
        "sig-flood", Signals(imd_cyclone_alert=True, river_gauge_pct_of_danger=130.0)
    )
    assert rt_eq.module is Module.EARTHQUAKE
    assert rt_fl.module is Module.CYCLONE_FLOOD

    mgr.run_cycles(2)
    assert mgr.get("sig-eq").snapshot().dispatches > 0
    assert mgr.get("sig-flood").snapshot().dispatches > 0


def test_activate_from_signals_with_no_trigger_raises():
    """A Signals snapshot that trips no activation predicate is an error."""
    mgr = IncidentManager()
    with pytest.raises(ValueError):
        mgr.activate("nothing", Signals())


def test_activate_rejects_wrong_type():
    mgr = IncidentManager()
    with pytest.raises(TypeError):
        mgr.activate("bad", object())  # type: ignore[arg-type]


# ----------------------------------------------------------------- snapshot shape
def test_snapshot_to_dict_is_jsonable_and_complete():
    import json

    mgr = IncidentManager()
    mgr.activate("eq", IncidentSeed.earthquake(magnitude=6.2))
    mgr.activate("flood", IncidentSeed.flood())
    mgr.run_cycles(2)

    snap = mgr.snapshot()
    d = snap.to_dict()
    # round-trips through JSON (deterministic, stdlib only)
    json.dumps(d)

    assert set(d) == {"incidents", "aggregate"}
    for iid in ("eq", "flood"):
        inc = d["incidents"][iid]
        assert set(inc) == {
            "incident_id",
            "module",
            "active",
            "cycles",
            "topic_counts",
            "dispatches",
            "escalations",
            "degraded_modules",
        }
        assert inc["incident_id"] == iid
        assert inc["module"] in {"A", "B"}
        assert inc["cycles"] == 2


def test_runtime_is_exposed_and_inspectable():
    mgr = IncidentManager()
    rt = mgr.activate("eq", IncidentSeed.earthquake(magnitude=6.2))
    assert isinstance(rt, IncidentRuntime)
    assert mgr.get("eq") is rt
    assert "eq" in mgr
    mgr.run_cycle()
    # the runtime's own snapshot mirrors the manager view
    s = rt.snapshot()
    assert s.incident_id == "eq"
    assert s.module is Module.EARTHQUAKE
    assert s.dispatches == len(rt.real_dispatches())
    assert s.topic_counts == rt.topic_counts()
