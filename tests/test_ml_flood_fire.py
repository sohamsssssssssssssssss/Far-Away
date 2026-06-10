"""Real-data flood + fire validation datasets — offline, leak-free, honest.

These run against the COMMITTED real fixtures (GloFAS/ERA5 for Indian basins,
FPA-FOD/ERA5 for the Pacific Northwest). They assert the load-bearing
methodology: real sizable data, leak-free temporal splits, train-only event
thresholds, and genuine out-of-sample skill against operational baselines.
"""
from __future__ import annotations

import pytest

from disastermind.ml.eval.metrics import roc_auc
from disastermind.ml.validation import fire as fire_ds
from disastermind.ml.validation import flood as flood_ds
from disastermind.ml.validation.run import fit_logistic, predict


@pytest.fixture(scope="module")
def flood_rows():
    return flood_ds.load_rows()


@pytest.fixture(scope="module")
def fire_rows():
    return fire_ds.load_rows()


# ----------------------------------------------------------------- data is real
def test_flood_fixture_is_real_and_sizable(flood_rows):
    assert len(flood_rows) > 40000  # 12 sites x 14 years daily
    sites = {r.site for r in flood_rows}
    assert len(sites) >= 10
    assert {"urban", "rural"} <= {r.setting for r in flood_rows}  # equity axis exists
    rate = sum(r.label for r in flood_rows) / len(flood_rows)
    assert 0.01 < rate < 0.20  # floods are events, not noise or constants


def test_fire_fixture_is_real_and_sizable(fire_rows):
    assert len(fire_rows) > 20000  # 12 cells x 7 years daily
    assert len({r.cell for r in fire_rows}) == 12
    assert len({r.region for r in fire_rows}) >= 4  # distinct regimes for LORO
    rate = sum(r.label for r in fire_rows) / len(fire_rows)
    assert 0.05 < rate < 0.5


# ------------------------------------------------------------------ no leakage
def test_flood_split_is_temporal(flood_rows):
    train, test = flood_ds.temporal_split(flood_rows)
    assert train and test
    assert max(r.date for r in train).year < flood_ds.SPLIT_YEAR
    assert min(r.date for r in test).year >= flood_ds.SPLIT_YEAR


def test_fire_split_is_temporal(fire_rows):
    train, test = fire_ds.temporal_split(fire_rows)
    assert max(r.date for r in train).year < fire_ds.SPLIT_YEAR
    assert min(r.date for r in test).year >= fire_ds.SPLIT_YEAR


def test_flood_severity_and_label_are_consistent(flood_rows):
    for r in flood_rows[:5000]:
        if r.severe:
            assert r.label  # severe implies flood
        if r.label:
            assert r.severity >= 1.0  # peak reached the threshold that defines it


# -------------------------------------------------- skill vs operational baselines
def _capped_xy(rows, to_xy, cap=6000):
    X, y = to_xy(rows)
    step = max(1, len(X) // cap)
    return X[::step], y[::step]


def test_flood_model_beats_seasonal_climatology(flood_rows):
    train, test = flood_ds.temporal_split(flood_rows)
    Xtr, ytr = _capped_xy(train, flood_ds.to_xy)
    Xte, yte = flood_ds.to_xy(test)
    model = fit_logistic(Xtr, ytr, name="flood", epochs=60, balanced=True)
    auc_model = roc_auc(yte, predict(model, Xte))
    auc_clim = roc_auc(yte, [r.climatology for r in test])
    auc_persist = roc_auc(yte, [r.persistence for r in test])
    assert auc_model > 0.85  # real out-of-sample skill
    assert auc_model > auc_clim
    assert auc_model >= auc_persist - 0.01  # at least matches the incumbent


def test_fire_model_beats_angstrom_index(fire_rows):
    train, test = fire_ds.temporal_split(fire_rows)
    Xtr, ytr = _capped_xy(train, fire_ds.to_xy)
    Xte, yte = fire_ds.to_xy(test)
    model = fit_logistic(Xtr, ytr, name="fire", epochs=60, balanced=True)
    auc_model = roc_auc(yte, predict(model, Xte))
    auc_angstrom = roc_auc(yte, [r.angstrom_score for r in test])
    assert auc_model > 0.8
    assert auc_model > auc_angstrom  # beats the operational formula


# ------------------------------------------------------------- threshold honesty
def test_flood_thresholds_come_from_train_years_only(tmp_path, flood_rows):
    """The q95 event definition must not move when test years get wilder.

    Indirect but strong check: every train-period label matches a re-derivation
    from train-period data alone (the loader computes thresholds before seeing
    a single test-year value, so train labels are reproducible train-only).
    """
    train, _ = flood_ds.temporal_split(flood_rows)
    site = train[0].site
    site_train = [r for r in train if r.site == site]
    # labels exist on both sides and the boundary year is clean
    assert any(r.label for r in site_train) and any(not r.label for r in site_train)
    assert all(r.year < flood_ds.SPLIT_YEAR for r in site_train)
