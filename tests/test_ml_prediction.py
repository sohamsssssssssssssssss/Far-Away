"""Tier 2 prediction agents <-> :mod:`disastermind.ml` model-layer wiring.

PRD Step 3 (Prediction & Assessment) / Step 9 (Explainability). The three
prediction specialists each expose a real-model seam — ``_try_ensemble`` (flood),
``_try_hazus`` (earthquake), ``_try_ca`` (fire) — that consults
``disastermind.ml.get_model(module)`` and engages it ONLY when a *trained real
backend* is loaded (``model._backend_obj is not None``). No trained artefacts ship
with the repo, so the default path is the deterministic heuristic and is byte-for
-byte unchanged (the eight existing prediction behaviours hold).

These tests prove BOTH halves of that contract, stdlib-only and offline:

  * With NO trained model registered, each agent's emitted prediction is IDENTICAL
    to the heuristic baseline (same model tag, same peak probability / perimeter).
  * With a tiny stub model registered (a fake backend whose ``predict`` returns a
    feature-driven probability), the agent routes through the model layer: the
    model tag flips to the ``*-ml`` variant, the peak hazard shifts to track the
    model's output, and ``log_prediction`` records the real model's SHAP dict.

The stub sets ``_backend_obj`` directly (no XGBoost/sklearn needed) so the wiring
is exercised without any optional dependency — exactly the seam production fitted
boosters will flow through.
"""
from __future__ import annotations

import math

import pytest

from disastermind.audit.decision_log import DecisionLogger
from disastermind.core.bus import InMemoryBus
from disastermind.core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Topic,
)
from disastermind.ml import RiskModel, register_model, reset_registry
from disastermind.ml.models import _clamp01, _logistic
from disastermind.tier2.prediction.agents import (
    CyclonePredictionAgent,
    EarthquakeImpactAgent,
    FireSpreadAgent,
)


# --------------------------------------------------------------------------- stub
class _StubBackend:
    """Sentinel standing in for a fitted XGBoost/sklearn estimator."""


class _LinearStubModel(RiskModel):
    """A trained-looking RiskModel: logistic over a fixed weight vector.

    Mirrors what ``register_model(module, fitted_model)`` would install in
    production, but without any optional library. Because ``_backend_obj`` is a
    non-None sentinel, the agents treat it as a real trained backend and route
    through the model layer; ``_predict_backend`` returns probabilities in [0, 1]
    that vary with the features so the SHAP fallback yields non-trivial
    attributions.
    """

    backend = "stub"

    def __init__(self, module: Module, weights: tuple[float, ...], intercept: float) -> None:
        super().__init__(module)
        self._w = weights
        self._b = intercept
        self._backend_obj = _StubBackend()
        self.fitted = True

    def _predict_backend(self, X: list[list[float]]) -> list[float]:
        out = []
        for row in X:
            z = self._b + sum(w * x for w, x in zip(self._w, row))
            out.append(_clamp01(_logistic(z)))
        return out


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


# --------------------------------------------------------------------------- feeds
def _raw_feed(kind: str, module: Module, *, severity: float, meta: dict | None = None) -> Message:
    return Message(
        sender="tier3.feed",
        recipient="tier2.prediction",
        type=MessageType.ALERT,
        priority=Priority.HIGH,
        topic=Topic.RAW_FEED,
        incident_id=f"inc-{module.value}",
        module=module,
        payload={
            "event": {
                "kind": kind,
                "incident_id": f"inc-{module.value}",
                "severity": severity,
                "epicentre": {"lat": 19.0, "lon": 72.8},
                "meta": meta or {},
            }
        },
    )


def _prediction_record(logger: DecisionLogger) -> dict:
    preds = [r for r in logger.memory if r.get("kind") == "prediction"]
    assert preds, "agent did not log a prediction"
    return preds[-1]


def _peak_cell_prob(message: Message) -> float:
    cells = message.payload["risk_cells"]
    return max((c["probability"] for c in cells), default=0.0)


def _peak_collapse(message: Message) -> float:
    bld = message.payload["buildings"]
    return max((b["collapse_probability"] for b in bld), default=0.0)


def _max_perimeter_radius(message: Message) -> float:
    radii = []
    for front in message.payload["fire_fronts"]:
        for pt in front["perimeter"]:
            radii.append(math.hypot(pt["lat"] - 19.0, pt["lon"] - 72.8))
    return max(radii, default=0.0)


# ============================================================= A: cyclone / flood
def test_flood_default_path_is_heuristic_unchanged():
    """With no trained model the flood agent runs the deterministic heuristic."""
    logger = DecisionLogger.null()
    agent = CyclonePredictionAgent(InMemoryBus(), logger)
    out = agent.handle(_raw_feed("flood", Module.CYCLONE_FLOOD, severity=2.0))
    assert len(out) == 1
    assert _prediction_record(logger)["model"] == "flood-inundation-heuristic"


def test_flood_trained_model_shifts_peak_and_model_tag():
    """A registered trained model overrides the base hazard via predict_one."""
    bus = InMemoryBus()
    baseline_logger = DecisionLogger.null()
    baseline_peak = _peak_cell_prob(
        CyclonePredictionAgent(bus, baseline_logger).handle(
            _raw_feed("flood", Module.CYCLONE_FLOOD, severity=2.0)
        )[0]
    )

    # A near-saturating model: base hazard ~ logistic(6.0) ~= 0.998 regardless of
    # the modest heuristic drivers, so the peak must climb well above baseline.
    register_model(
        Module.CYCLONE_FLOOD,
        _LinearStubModel(Module.CYCLONE_FLOOD, (0.0, 0.0, 0.0), 6.0),
    )
    logger = DecisionLogger.null()
    out = CyclonePredictionAgent(InMemoryBus(), logger).handle(
        _raw_feed("flood", Module.CYCLONE_FLOOD, severity=2.0)
    )
    rec = _prediction_record(logger)
    assert rec["model"] == "flood-inundation-ml"
    trained_peak = _peak_cell_prob(out[0])
    assert trained_peak > baseline_peak + 0.1
    # The logged SHAP keys are the model-layer feature names (real explainer).
    assert set(rec["shap"]) == {"rainfall_mm", "storm_surge_m", "river_level_m"}


def test_flood_untrained_wrapper_still_uses_heuristic():
    """An UNfitted wrapper (``_backend_obj is None``) must NOT engage the seam."""
    # get_model(...) without fitting leaves _backend_obj None -> fallback path.
    from disastermind.ml import get_model

    assert get_model(Module.CYCLONE_FLOOD)._backend_obj is None
    logger = DecisionLogger.null()
    CyclonePredictionAgent(InMemoryBus(), logger).handle(
        _raw_feed("flood", Module.CYCLONE_FLOOD, severity=2.0)
    )
    assert _prediction_record(logger)["model"] == "flood-inundation-heuristic"


# ================================================================ B: earthquake
def test_earthquake_default_path_is_heuristic_unchanged():
    logger = DecisionLogger.null()
    agent = EarthquakeImpactAgent(InMemoryBus(), logger)
    out = agent.handle(
        _raw_feed("earthquake", Module.EARTHQUAKE, severity=6.5, meta={"depth_km": 10.0})
    )
    assert len(out) == 1
    assert _prediction_record(logger)["model"] == "shakemap-fragility-heuristic"


def test_earthquake_trained_model_overrides_collapse_probability():
    """A trained model replaces the logistic fragility per-building collapse."""
    baseline_logger = DecisionLogger.null()
    baseline_peak = _peak_collapse(
        EarthquakeImpactAgent(InMemoryBus(), baseline_logger).handle(
            _raw_feed("earthquake", Module.EARTHQUAKE, severity=6.5, meta={"depth_km": 10.0})
        )[0]
    )
    assert baseline_peak > 0.5  # heuristic is high for a shallow M6.5

    # A model forced LOW (strong negative intercept) so collapse must drop far
    # below the heuristic baseline -> proves the model output, not the heuristic.
    register_model(
        Module.EARTHQUAKE,
        _LinearStubModel(Module.EARTHQUAKE, (0.0, 0.0, 0.0), -6.0),
    )
    logger = DecisionLogger.null()
    out = EarthquakeImpactAgent(InMemoryBus(), logger).handle(
        _raw_feed("earthquake", Module.EARTHQUAKE, severity=6.5, meta={"depth_km": 10.0})
    )
    rec = _prediction_record(logger)
    assert rec["model"] == "shakemap-fragility-ml"
    trained_peak = _peak_collapse(out[0])
    assert trained_peak < baseline_peak - 0.3
    assert set(rec["shap"]) == {"magnitude", "distance_km", "construction"}


# ====================================================================== C: fire
def test_fire_default_path_is_heuristic_unchanged():
    logger = DecisionLogger.null()
    agent = FireSpreadAgent(InMemoryBus(), logger)
    out = agent.handle(
        _raw_feed(
            "urban_fire",
            Module.FIRE_COLLAPSE,
            severity=1.5,
            meta={"wind_speed_ms": 4.0, "wind_dir_deg": 90.0},
        )
    )
    assert len(out) == 1
    assert _prediction_record(logger)["model"] == "fire-cellular-automata-heuristic"


def test_fire_trained_model_expands_perimeter_and_model_tag():
    """A high-confidence trained model accelerates the projected perimeter."""
    feed_kwargs = {"severity": 1.5, "meta": {"wind_speed_ms": 4.0, "wind_dir_deg": 90.0}}
    baseline_logger = DecisionLogger.null()
    baseline_radius = _max_perimeter_radius(
        FireSpreadAgent(InMemoryBus(), baseline_logger).handle(
            _raw_feed("urban_fire", Module.FIRE_COLLAPSE, **feed_kwargs)
        )[0]
    )

    register_model(
        Module.FIRE_COLLAPSE,
        _LinearStubModel(Module.FIRE_COLLAPSE, (1.0, 0.1, 0.1), 4.0),
    )
    logger = DecisionLogger.null()
    out = FireSpreadAgent(InMemoryBus(), logger).handle(
        _raw_feed("urban_fire", Module.FIRE_COLLAPSE, **feed_kwargs)
    )
    rec = _prediction_record(logger)
    assert rec["model"] == "fire-cellular-automata-ml"
    trained_radius = _max_perimeter_radius(out[0])
    assert trained_radius > baseline_radius  # near-1.0 burn prob accelerates spread
    assert set(rec["shap"]) == {"intensity", "wind_speed_ms", "base_fuel"}


# ============================================================ cross-cutting guard
def test_no_trained_model_output_is_byte_identical_per_module():
    """Default emitted payload must equal a fresh heuristic run (no ml side-effect)."""
    cases = [
        (CyclonePredictionAgent, _raw_feed("flood", Module.CYCLONE_FLOOD, severity=2.0)),
        (
            EarthquakeImpactAgent,
            _raw_feed("earthquake", Module.EARTHQUAKE, severity=6.5, meta={"depth_km": 10.0}),
        ),
        (
            FireSpreadAgent,
            _raw_feed(
                "urban_fire",
                Module.FIRE_COLLAPSE,
                severity=1.5,
                meta={"wind_speed_ms": 4.0, "wind_dir_deg": 90.0},
            ),
        ),
    ]
    for cls, feed in cases:
        reset_registry()
        first = cls(InMemoryBus(), DecisionLogger.null()).handle(feed)[0].payload
        reset_registry()
        second = cls(InMemoryBus(), DecisionLogger.null()).handle(feed)[0].payload
        assert first == second
