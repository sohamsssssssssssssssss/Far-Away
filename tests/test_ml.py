"""Tests for :mod:`disastermind.ml` — the model layer behind the prediction
heuristics (PRD Step 3 / Step 9).

Stdlib only: every assertion below holds with NO optional dependency and NO
network. Tests that exercise the *real* XGBoost / scikit-learn / SHAP backends are
guarded with :func:`pytest.importorskip`, so they are skipped (not failed) when
those libraries are absent.
"""
from __future__ import annotations

import math
import os

import pytest

from disastermind.core.contracts import Module
from disastermind.ml import (
    DEFAULT_BACKENDS,
    FEATURE_NAMES,
    Explanation,
    FeatureError,
    FeatureVector,
    HeuristicRiskModel,
    RiskModel,
    SklearnRiskModel,
    XGBoostRiskModel,
    all_models,
    baseline_row,
    explain,
    explain_dict,
    feature_matrix,
    features_for_event,
    features_for_module,
    fire_features,
    flood_features,
    get_model,
    heuristic_probability,
    load_model,
    model_class_for,
    quake_features,
    register_model,
    reset_registry,
)

ALL_MODULES = (Module.EARTHQUAKE, Module.CYCLONE_FLOOD, Module.FIRE_COLLAPSE)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


# --------------------------------------------------------------------------- features
def test_feature_names_schema_frozen():
    assert FEATURE_NAMES[Module.EARTHQUAKE] == ("magnitude", "distance_km", "construction")
    assert FEATURE_NAMES[Module.CYCLONE_FLOOD] == (
        "rainfall_mm",
        "storm_surge_m",
        "river_level_m",
    )
    assert FEATURE_NAMES[Module.FIRE_COLLAPSE] == ("intensity", "wind_speed_ms", "base_fuel")


@pytest.mark.parametrize(
    "builder,module",
    [
        (lambda: quake_features(7.0, 10.0, "rcc"), Module.EARTHQUAKE),
        (lambda: flood_features(200.0, 2.0, 4.0), Module.CYCLONE_FLOOD),
        (lambda: fire_features(2.0, 10.0, 1.5), Module.FIRE_COLLAPSE),
    ],
)
def test_feature_vector_shape_and_names(builder, module):
    fv = builder()
    assert isinstance(fv, FeatureVector)
    assert fv.module is module
    assert fv.names == FEATURE_NAMES[module]
    assert fv.dim == len(FEATURE_NAMES[module]) == 3
    assert len(fv.as_list()) == 3
    assert list(fv.as_dict().keys()) == list(FEATURE_NAMES[module])
    assert all(isinstance(v, float) for v in fv.values)


def test_feature_vector_length_mismatch_raises():
    with pytest.raises(FeatureError):
        FeatureVector(Module.EARTHQUAKE, ("a", "b"), (1.0,))


def test_construction_ordinal_monotone():
    # More resilient construction -> higher ordinal value in the quake vector.
    kutcha = quake_features(7.0, 10.0, "kutcha").as_dict()["construction"]
    pucca = quake_features(7.0, 10.0, "pucca").as_dict()["construction"]
    rcc = quake_features(7.0, 10.0, "rcc").as_dict()["construction"]
    unknown = quake_features(7.0, 10.0, "garbage").as_dict()["construction"]
    assert kutcha < pucca < rcc
    assert kutcha < unknown < pucca  # unknown defaults to the mid ordinal


def test_features_for_module_uses_synonyms_and_defaults():
    fv = features_for_module(Module.CYCLONE_FLOOD, {"rainfall_mm": 120, "surge_m": 3})
    d = fv.as_dict()
    assert d["rainfall_mm"] == 120.0
    assert d["storm_surge_m"] == 3.0  # synonym mapped
    assert d["river_level_m"] == 0.0  # default


def test_features_for_event_infers_module():
    fv = features_for_event({"kind": "earthquake", "severity": 6.5, "meta": {"distance_km": 12}})
    assert fv.module is Module.EARTHQUAKE
    d = fv.as_dict()
    assert d["magnitude"] == 6.5
    assert d["distance_km"] == 12.0


def test_features_for_event_unknown_kind_raises():
    with pytest.raises(FeatureError):
        features_for_event({"kind": "volcano", "severity": 1.0})


def test_feature_matrix_batch_shape():
    rows = [
        {"rainfall_mm": 100, "storm_surge_m": 1, "river_level_m": 2},
        {"rainfall_mm": 300, "storm_surge_m": 4, "river_level_m": 6},
    ]
    X = feature_matrix(Module.CYCLONE_FLOOD, rows)
    assert len(X) == 2
    assert all(len(r) == 3 for r in X)
    assert X[0] == [100.0, 1.0, 2.0]


# --------------------------------------------------------------------------- heuristic
@pytest.mark.parametrize("module", ALL_MODULES)
def test_heuristic_probability_in_range_and_deterministic(module):
    row = [1.0, 1.0, 1.0]
    p1 = heuristic_probability(module, row)
    p2 = heuristic_probability(module, row)
    assert p1 == p2  # deterministic
    assert 0.0 <= p1 <= 1.0  # in range


def test_heuristic_probability_unknown_module_raises():
    class _Fake:
        pass

    with pytest.raises(ValueError):
        heuristic_probability(_Fake(), [1.0, 2.0, 3.0])  # type: ignore[arg-type]


def test_heuristic_monotonicity_per_dominant_driver():
    # Earthquake: higher magnitude -> higher collapse probability.
    low = get_model(Module.EARTHQUAKE).predict_one(quake_features(5.0, 10.0, "pucca"))
    high = get_model(Module.EARTHQUAKE).predict_one(quake_features(8.0, 10.0, "pucca"))
    assert high > low
    # Flood: more rainfall -> higher inundation probability.
    fl = get_model(Module.CYCLONE_FLOOD)
    assert fl.predict_one(flood_features(400, 2, 4)) > fl.predict_one(flood_features(50, 2, 4))
    # Fire: more wind -> higher burn probability.
    fc = get_model(Module.FIRE_COLLAPSE)
    assert fc.predict_one(fire_features(2, 20, 2)) > fc.predict_one(fire_features(2, 2, 2))


# --------------------------------------------------------------------------- models
@pytest.mark.parametrize("module", ALL_MODULES)
def test_unfitted_model_predicts_via_heuristic_in_range(module):
    model = HeuristicRiskModel(module)
    X = [[1.0, 1.0, 1.0], [5.0, 5.0, 5.0]]
    preds = model.predict(X)
    assert len(preds) == 2
    assert all(0.0 <= p <= 1.0 for p in preds)
    # Matches the standalone heuristic exactly (same output shape as agents).
    assert preds == [heuristic_probability(module, X[0]), heuristic_probability(module, X[1])]


@pytest.mark.parametrize("module", ALL_MODULES)
def test_predict_deterministic(module):
    model = get_model(module)
    fv = features_for_module(module, {})
    a = model.predict_one(fv)
    b = model.predict_one(fv)
    assert a == b
    assert 0.0 <= a <= 1.0


def test_model_rejects_unsupported_module():
    class _Fake:
        pass

    with pytest.raises(ValueError):
        RiskModel(_Fake())  # type: ignore[arg-type]


def test_fit_wrong_feature_count_raises():
    model = HeuristicRiskModel(Module.EARTHQUAKE)
    with pytest.raises(ValueError):
        model.fit([[1.0, 2.0]], [1.0])  # only 2 of 3 features


def test_fit_xy_length_mismatch_raises():
    model = HeuristicRiskModel(Module.EARTHQUAKE)
    with pytest.raises(ValueError):
        model.fit([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], [1.0])


def test_fit_without_backend_marks_heuristic_fitted():
    model = HeuristicRiskModel(Module.FIRE_COLLAPSE)
    out = model.fit([[1.0, 2.0, 3.0]], [1.0])
    assert out is model
    assert model.fitted is True
    assert model._backend_obj is None  # no real backend installed for heuristic


def test_manifest_shape():
    model = HeuristicRiskModel(Module.EARTHQUAKE)
    man = model.manifest()
    assert man["module"] == Module.EARTHQUAKE.value
    assert man["feature_names"] == list(FEATURE_NAMES[Module.EARTHQUAKE])
    assert man["backend"] == "heuristic"


def test_save_load_roundtrip_heuristic(tmp_path):
    model = HeuristicRiskModel(Module.CYCLONE_FLOOD)
    model.fit([[100.0, 1.0, 2.0]], [1.0])
    path = os.path.join(str(tmp_path), "flood_model.json")
    assert model.save(path) == path
    assert os.path.exists(path)
    restored = RiskModel.load(path)
    assert restored.module is Module.CYCLONE_FLOOD
    assert restored.feature_names == FEATURE_NAMES[Module.CYCLONE_FLOOD]
    fv = flood_features(200, 3, 5)
    assert restored.predict_one(fv) == model.predict_one(fv)


# --------------------------------------------------------------------------- registry
@pytest.mark.parametrize("module", ALL_MODULES)
def test_get_model_per_module(module):
    model = get_model(module)
    assert isinstance(model, RiskModel)
    assert model.module is module
    assert model.feature_names == FEATURE_NAMES[module]


def test_get_model_caches_instance():
    a = get_model(Module.EARTHQUAKE)
    b = get_model(Module.EARTHQUAKE)
    assert a is b
    c = get_model(Module.EARTHQUAKE, fresh=True)
    assert c is not a


def test_get_model_accepts_value_and_name():
    by_value = get_model("B")
    by_name = get_model("EARTHQUAKE")
    assert by_value.module is Module.EARTHQUAKE
    assert by_name.module is Module.EARTHQUAKE


def test_get_model_unknown_raises():
    with pytest.raises(ValueError):
        get_model("nope")


def test_default_backends_assignment():
    assert model_class_for(Module.CYCLONE_FLOOD) is XGBoostRiskModel
    assert model_class_for(Module.EARTHQUAKE) is XGBoostRiskModel
    assert model_class_for(Module.FIRE_COLLAPSE) is SklearnRiskModel
    assert set(DEFAULT_BACKENDS) == set(ALL_MODULES)


def test_all_models_covers_every_module():
    models = all_models()
    assert set(models) == set(ALL_MODULES)
    assert all(m.module is k for k, m in models.items())


def test_register_model_validation():
    good = HeuristicRiskModel(Module.FIRE_COLLAPSE)
    assert register_model(Module.FIRE_COLLAPSE, good) is good
    assert get_model(Module.FIRE_COLLAPSE) is good
    # module mismatch
    with pytest.raises(ValueError):
        register_model(Module.EARTHQUAKE, good)


def test_load_model_registers(tmp_path):
    model = HeuristicRiskModel(Module.EARTHQUAKE)
    path = os.path.join(str(tmp_path), "quake.json")
    model.save(path)
    loaded = load_model(Module.EARTHQUAKE, path)
    assert get_model(Module.EARTHQUAKE) is loaded


# --------------------------------------------------------------------------- shap
@pytest.mark.parametrize("module", ALL_MODULES)
def test_explain_fallback_dict_shape(module):
    model = get_model(module)
    fv = features_for_module(module, {})
    exp = explain(model, fv)
    assert isinstance(exp, Explanation)
    assert exp.method == "fallback"
    # Same dict shape agents log to log_prediction: keys == feature names.
    assert set(exp.attributions) == set(FEATURE_NAMES[module])
    assert list(exp.as_dict().keys()) == list(FEATURE_NAMES[module])
    assert all(isinstance(v, float) for v in exp.attributions.values())


def test_explain_fallback_is_additive_and_deterministic():
    model = get_model(Module.EARTHQUAKE)
    fv = quake_features(7.5, 5.0, "kutcha")
    e1 = explain(model, fv)
    e2 = explain(model, fv)
    assert e1.attributions == e2.attributions  # deterministic
    # base_value + sum(attributions) ~= prediction (SHAP additivity).
    total = e1.base_value + sum(e1.attributions.values())
    assert math.isclose(total, e1.prediction, abs_tol=1e-5)
    assert 0.0 <= e1.prediction <= 1.0
    assert e1.top_feature() in FEATURE_NAMES[Module.EARTHQUAKE]


def test_explain_dict_convenience():
    model = get_model(Module.FIRE_COLLAPSE)
    d = explain_dict(model, fire_features(3, 20, 2))
    assert set(d) == set(FEATURE_NAMES[Module.FIRE_COLLAPSE])


def test_explain_wrong_dimension_raises():
    model = get_model(Module.EARTHQUAKE)
    with pytest.raises(ValueError):
        explain(model, [1.0, 2.0])  # only 2 features


def test_baseline_row_shape():
    for module in ALL_MODULES:
        base = baseline_row(module)
        assert len(base) == len(FEATURE_NAMES[module])


def test_explain_empty_attributions_when_no_signal():
    # A row equal to its baseline yields a near-zero gap -> still additive & in range.
    model = get_model(Module.CYCLONE_FLOOD)
    base = baseline_row(Module.CYCLONE_FLOOD)
    exp = explain(model, base)
    total = exp.base_value + sum(exp.attributions.values())
    assert math.isclose(total, exp.prediction, abs_tol=1e-5)


# --------------------------------------------------------------------- optional backends
def test_xgboost_backend_fits_when_available():
    pytest.importorskip("xgboost")
    pytest.importorskip("numpy")
    model = XGBoostRiskModel(Module.EARTHQUAKE)
    X = [[m, 20.0, 1.0] for m in (4.0, 5.0, 6.0, 7.0, 8.0, 9.0)]
    y = [0.0, 0.1, 0.3, 0.6, 0.8, 0.95]
    model.fit(X, y)
    preds = model.predict([[8.5, 20.0, 1.0]])
    assert len(preds) == 1
    assert 0.0 <= preds[0] <= 1.0


def test_sklearn_backend_fits_when_available():
    pytest.importorskip("sklearn")
    pytest.importorskip("numpy")
    model = SklearnRiskModel(Module.FIRE_COLLAPSE)
    X = [[i, w, 1.0] for i, w in [(0.5, 1), (1.0, 5), (2.0, 15), (3.0, 25), (0.2, 0), (2.8, 20)]]
    y = [0.0, 0.2, 0.7, 0.95, 0.0, 0.9]
    model.fit(X, y)
    preds = model.predict([[2.5, 18.0, 1.0]])
    assert len(preds) == 1
    assert 0.0 <= preds[0] <= 1.0


def test_shap_explanation_when_available():
    pytest.importorskip("shap")
    pytest.importorskip("xgboost")
    pytest.importorskip("numpy")
    model = XGBoostRiskModel(Module.EARTHQUAKE)
    X = [[m, 20.0, 1.0] for m in (4.0, 5.0, 6.0, 7.0, 8.0, 9.0)]
    y = [0.0, 0.1, 0.3, 0.6, 0.8, 0.95]
    model.fit(X, y)
    exp = explain(model, quake_features(8.5, 20.0, "pucca"))
    # Whether shap really ran or fell back, the dict shape must be stable.
    assert set(exp.attributions) == set(FEATURE_NAMES[Module.EARTHQUAKE])
    assert exp.method in ("shap", "fallback")
