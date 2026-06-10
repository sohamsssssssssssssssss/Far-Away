"""Real-data validation tests (PRD Step 3) — offline, deterministic.

These run against the COMMITTED real USGS catalog fixture (no network). To stay
fast in the suite we train on a capped subsample with fewer epochs, but still
assert the load-bearing methodology + that the model shows GENUINE out-of-sample
skill on real earthquakes (AUC above no-skill, no worse than a magnitude-only
baseline) with physically-sensible learned weights.
"""
from __future__ import annotations

from disastermind.ml.eval.metrics import roc_auc
from disastermind.ml.validation import (
    FEATURE_NAMES,
    fit_logistic,
    load_quakes,
    predict,
    temporal_split,
    to_xy,
)
from disastermind.ml.validation.dataset import SPLIT_EPOCH_MS


def _fast_split(cap: int = 4000):
    quakes = load_quakes()
    train, test = temporal_split(quakes)
    return train[:cap], test[:cap]


# --------------------------------------------------------------- data is real
def test_fixture_is_real_and_sizable():
    quakes = load_quakes()
    assert len(quakes) > 30000  # the real 2013-2017 M4.5+ catalog
    assert all(q.mag >= 4.5 for q in quakes)  # the catalog floor
    assert any(q.label() == 1 for q in quakes) and any(q.label() == 0 for q in quakes)


def test_temporal_split_has_no_leakage():
    train, test = temporal_split(load_quakes())
    assert train and test
    assert max(q.time for q in train) < SPLIT_EPOCH_MS <= min(q.time for q in test)


def test_features_are_leak_free():
    """Feature vector must be physical-only — never the outcome fields."""
    assert FEATURE_NAMES == (
        "magnitude",
        "depth_km",
        "abs_latitude",
        "ocean_proxy",
        "gmpe_attenuation",
    )
    q = load_quakes()[0]
    feats = q.features()
    assert len(feats) == 5
    # gmpe_attenuation derives only from magnitude + depth (pre-event physics),
    # and the label-bearing outcome fields (felt/alert/tsunami/mmi) are unused.
    assert feats[4] == q.gmpe_score()


# ----------------------------------------------------- genuine out-of-sample skill
def test_model_has_real_out_of_sample_skill():
    train, test = _fast_split()
    Xtr, ytr = to_xy(train)
    Xte, yte = to_xy(test)
    model = fit_logistic(Xtr, ytr, name="m", epochs=80)
    auc = roc_auc(yte, predict(model, Xte))
    # REAL skill on REAL held-out earthquakes — comfortably above no-skill (0.5).
    assert auc > 0.6, f"out-of-sample AUC {auc:.3f} shows no real skill"


def test_model_at_least_matches_magnitude_baseline():
    train, test = _fast_split()
    Xtr, ytr = to_xy(train)
    Xte, yte = to_xy(test)
    model = fit_logistic(Xtr, ytr, name="m", epochs=80)
    base = fit_logistic([[r[0]] for r in Xtr], ytr, name="b", epochs=80)
    auc_m = roc_auc(yte, predict(model, Xte))
    auc_b = roc_auc(yte, predict(base, [[r[0]] for r in Xte]))
    assert auc_m >= auc_b - 0.01  # the richer model never meaningfully underperforms


def test_learned_model_is_physically_sensible():
    """Behavioural check: more shaking in, more risk out.

    Magnitude and the GMPE attenuation feature are deliberately collinear (the
    baseline is stacked as a feature), so individual weight signs can trade off
    against each other; what must hold is the physics of the OUTPUT: a large
    shallow quake scores far above a small deep one.
    """
    train, _ = _fast_split()
    Xtr, ytr = to_xy(train)
    model = fit_logistic(Xtr, ytr, name="m", epochs=120)
    big_shallow = next(q for q in train if q.mag >= 6.5 and q.depth_km < 50).features()
    small_deep = next(q for q in train if q.mag <= 4.6 and q.depth_km > 300).features()
    p_big, p_small = predict(model, [big_shallow, small_deep])
    assert p_big > p_small
    # and the shaking-side signal (magnitude + stacked GMPE) pulls risk UP overall
    w = dict(zip(FEATURE_NAMES, model.weights))
    assert w["magnitude"] + w["gmpe_attenuation"] > 0
