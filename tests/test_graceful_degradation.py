"""Graceful-degradation / fallback guarantees (PRD Step 10) — fully offline.

DisasterMind's core runtime is stdlib-only and must "degrade gracefully rather
than fail" when its optional, heavy dependencies (xgboost / sklearn trained
backends, confluent_kafka, psycopg, a live broker, a real network) are absent or
unreachable. This module proves that contract directly at the seams where it
lives, complementing the wiring-level coverage in ``test_live_ingest.py`` /
``test_resilient_polling.py`` and the agent-level coverage in
``test_ml_prediction.py``:

  1. Model fallback (``disastermind.ml``): the registry's wrappers answer
     ``predict`` / ``predict_one`` with a valid, deterministic, monotone
     probability in [0, 1] via the stdlib heuristic when no *trained backend*
     is loaded — regardless of whether an ML library happens to be installed.
  2. Bus / integration fallback (``disastermind.core.bus`` /
     ``disastermind.integrations``): a ``KafkaBus`` and ``KafkaRoundTrip`` pointed
     at an unreachable / unconfigured broker degrade to an in-memory store and
     still deliver / round-trip messages instead of crashing.
  3. Optional-dependency imports: the package's integration modules import with
     NO import-time dependency and NO import-time network, and ``ping_backends``
     reports every backend ``absent`` (never raises) on a bare install.

Everything here is stdlib-only, deterministic, and never opens a socket to a
reachable peer (HARD RULE 2): the only "network" is a connect attempt to an
unroutable ``127.0.0.1:1`` that is *expected* to fail and degrade.
"""
from __future__ import annotations

import importlib

import pytest

from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.ml.features import fire_features, flood_features, quake_features
from disastermind.ml.models import RiskModel
from disastermind.ml.registry import get_model, reset_registry

# Every supported module + a feature builder that drives its dominant risk driver
# from a clearly-low to a clearly-high value, so the heuristic's monotonicity is
# testable without depending on the exact tuned weights.
_LOW_HIGH = {
    Module.EARTHQUAKE: (
        lambda: quake_features(magnitude=3.0, distance_km=300.0, construction="reinforced"),
        lambda: quake_features(magnitude=8.5, distance_km=5.0, construction="unreinforced"),
    ),
    Module.CYCLONE_FLOOD: (
        lambda: flood_features(rainfall_mm=5.0, storm_surge_m=0.0, river_level_m=0.0),
        lambda: flood_features(rainfall_mm=450.0, storm_surge_m=6.0, river_level_m=8.0),
    ),
    Module.FIRE_COLLAPSE: (
        lambda: fire_features(intensity=0.2, wind_speed_ms=0.0, base_fuel=0.5),
        lambda: fire_features(intensity=3.0, wind_speed_ms=30.0, base_fuel=3.0),
    ),
}


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with an empty wrapper cache (no leaked fitted models)."""
    reset_registry()
    yield
    reset_registry()


# ============================================================ model fallback ===
@pytest.mark.parametrize("module", list(_LOW_HIGH))
def test_get_model_returns_unfitted_heuristic_with_no_trained_backend(module):
    """get_model defaults to a wrapper with NO trained backend (heuristic path).

    No trained artefacts ship with the repo, so the registry's per-module wrapper
    is constructed unfitted: ``_backend_obj is None`` and ``fitted is False``. This
    is the precondition that forces ``predict`` down the deterministic heuristic
    regardless of whether xgboost/sklearn are installed.
    """
    model = get_model(module)
    assert model._backend_obj is None, "a trained backend leaked into the default wrapper"
    assert model.fitted is False


@pytest.mark.parametrize("module", list(_LOW_HIGH))
def test_predict_one_is_a_valid_probability_with_no_backend(module):
    """predict_one always yields a probability in [0, 1] on the heuristic fallback."""
    low_fv, high_fv = (f() for f in _LOW_HIGH[module])
    model = get_model(module)
    for fv in (low_fv, high_fv):
        p = model.predict_one(fv)
        assert isinstance(p, float)
        assert 0.0 <= p <= 1.0, f"{module}: heuristic probability {p} out of [0, 1]"


@pytest.mark.parametrize("module", list(_LOW_HIGH))
def test_heuristic_fallback_is_deterministic(module):
    """The stdlib heuristic is pure: identical inputs give an identical probability."""
    low_fv, _high = (f() for f in _LOW_HIGH[module])
    first = get_model(module).predict_one(low_fv)
    reset_registry()
    second = get_model(module).predict_one(low_fv)
    assert first == second


@pytest.mark.parametrize("module", list(_LOW_HIGH))
def test_heuristic_fallback_is_monotone_in_its_drivers(module):
    """A clearly-worse hazard yields a strictly higher heuristic probability.

    Proves the fallback is a *meaningful* model, not a constant: each module's
    dominant drivers (magnitude/proximity, rainfall/surge/river, intensity/wind)
    push the probability up, so degradation preserves signal rather than collapsing
    to a fixed number.
    """
    low_fv, high_fv = (f() for f in _LOW_HIGH[module])
    model = get_model(module)
    assert model.predict_one(high_fv) > model.predict_one(low_fv)


def test_predict_batch_clamps_and_matches_predict_one():
    """RiskModel.predict returns one in-range probability per row, == predict_one."""
    model = RiskModel(Module.EARTHQUAKE)  # bare base model: heuristic only
    rows = [[8.5, 5.0, 0.0], [3.0, 300.0, 2.0]]
    probs = model.predict(rows)
    assert len(probs) == len(rows)
    assert all(0.0 <= p <= 1.0 for p in probs)
    # The single-row convenience must agree with the batch path element-wise.
    assert [model.predict_one(r) for r in rows] == probs


def test_failing_backend_hook_falls_back_to_heuristic():
    """A wrapper whose backend predict hook returns None degrades to the heuristic.

    ``RiskModel._predict_backend`` returning ``None`` is the documented "fall back"
    signal. We register a backend sentinel (so the seam is engaged) whose hook
    returns ``None``, and assert the output is exactly the heuristic — never an
    error, never a non-probability.
    """
    fv = quake_features(magnitude=8.5, distance_km=5.0, construction="unreinforced")
    expected = RiskModel(Module.EARTHQUAKE).predict_one(fv)  # pure heuristic baseline

    class _DeadBackend(RiskModel):
        backend = "dead"

        def __init__(self, module):
            super().__init__(module)
            self._backend_obj = object()  # engage the backend seam...

        def _predict_backend(self, X):
            return None  # ...but signal "fall back" every time.

    model = _DeadBackend(Module.EARTHQUAKE)
    assert model._backend_obj is not None
    got = model.predict_one(fv)
    assert got == expected
    assert 0.0 <= got <= 1.0


def test_saved_model_with_unloadable_backend_loads_as_heuristic(tmp_path):
    """A persisted wrapper whose backend artefact can't be restored still predicts.

    ``RiskModel.load`` degrades to the heuristic when the named backend's artefact
    is missing/unloadable, so a model round-trips and answers ``predict`` even when
    the optional library that wrote it is gone.
    """
    path = str(tmp_path / "model.json")
    saved = get_model(Module.CYCLONE_FLOOD)  # xgboost wrapper, unfitted -> heuristic
    saved.save(path)

    loaded = RiskModel.load(path)
    assert loaded.module is Module.CYCLONE_FLOOD
    assert loaded._backend_obj is None  # no artefact => heuristic
    fv = flood_features(rainfall_mm=450.0, storm_surge_m=6.0, river_level_m=8.0)
    p = loaded.predict_one(fv)
    assert 0.0 <= p <= 1.0
    # Round-trips identically to a fresh unfitted wrapper of the same module.
    assert p == get_model(Module.CYCLONE_FLOOD).predict_one(fv)


# ===================================================== bus / integration fallback
def test_kafka_bus_degrades_to_in_memory_when_broker_unreachable():
    """KafkaBus with no reachable broker degrades and still delivers messages.

    Pointed at an unroutable endpoint the client cannot connect, so the bus marks
    itself degraded, uses an in-memory fan-out, and a published message is still
    delivered to a subscriber — the single-process loop keeps functioning.
    """
    from disastermind.core.bus import KafkaBus

    bus = KafkaBus(brokers="127.0.0.1:1")  # unroutable: connect must fail
    assert bus.degraded is True
    assert bus._producer is None

    received: list[Message] = []
    bus.subscribe(Topic.RAW_FEED, "subscriber", lambda m: received.append(m))
    msg = Message(
        sender="ingest.test",
        recipient="tier2.prediction",
        type=MessageType.QUERY,
        priority=Priority.INFO,
        topic=Topic.RAW_FEED,
        module=Module.ALL,
        payload={"kind": "test"},
    )
    bus.publish(msg)

    assert received == [msg], "degraded KafkaBus did not deliver via in-memory fallback"


def test_kafka_roundtrip_uses_in_memory_fallback_and_round_trips():
    """KafkaRoundTrip with no bootstrap is in-memory and round-trips a Message dict."""
    from disastermind.integrations.kafka import KafkaRoundTrip

    rt = KafkaRoundTrip()  # no bootstrap, connect=False -> offline fallback
    assert rt.is_fallback is True

    msg = Message(
        sender="a",
        recipient="b",
        type=MessageType.ALERT,
        priority=Priority.CRITICAL,
        topic=Topic.RAW_FEED,
        module=Module.EARTHQUAKE,
        payload={"magnitude": 5.3},
    )
    got = rt.roundtrip("dm.raw", msg)
    assert got["payload"]["magnitude"] == 5.3
    assert got["sender"] == "a"


def test_kafka_roundtrip_connect_to_unreachable_broker_still_degrades():
    """Requesting connect=True against an unroutable broker degrades, never raises."""
    from disastermind.integrations.kafka import KafkaRoundTrip

    rt = KafkaRoundTrip("127.0.0.1:1", connect=True)  # client absent or unreachable
    assert rt.is_fallback is True  # degraded to the in-memory store
    # Still usable offline.
    rt.produce("t", {"id": "m1", "payload": {"x": 1}})
    assert rt.consume("t", max_messages=1)[0]["payload"]["x"] == 1


# ===================================================== optional-dependency imports
@pytest.mark.parametrize(
    "module_path",
    [
        "disastermind.integrations.kafka",
        "disastermind.integrations.sql",
        "disastermind.integrations.health",
        "disastermind.core.bus",
        "disastermind.live",
        "disastermind.ml.registry",
    ],
)
def test_modules_import_without_optional_dependencies(module_path):
    """Core/integration modules import with no import-time dep and no network.

    These modules' optional clients (confluent_kafka, psycopg, an ES/HTTP client)
    are imported lazily inside methods, so the import itself must succeed on a bare
    stdlib install. A hard failure here would break the whole "stdlib-only core".
    """
    mod = importlib.import_module(module_path)
    assert mod is not None


def test_ping_backends_reports_all_absent_on_bare_install_and_never_raises():
    """With default (unconfigured) settings every external backend is 'absent'.

    ``ping_backends`` must never raise — an unconfigured / library-absent backend
    is reported ``absent`` so the system knows to run on its in-memory fallbacks.
    """
    from disastermind.core.config import Settings
    from disastermind.integrations.health import ABSENT, ping_backends

    states = ping_backends(Settings())
    assert states, "ping_backends returned no backends"
    assert all(state == ABSENT for state in states.values()), states
