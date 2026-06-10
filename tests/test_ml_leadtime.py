"""Lead-time-vs-POD validation — actionable warning, not just accuracy."""
from __future__ import annotations

import pytest

from disastermind.ml.eval.leadtime import (
    actionable_lead_time,
    lead_time_curve,
    risk_trajectory,
    to_dict,
)
from disastermind.ml.validation import flood as F
from disastermind.ml.validation.run import fit_logistic, predict


def _factory(epochs: int = 50):
    def fit(X, y):
        m = fit_logistic(X, y, name="lt", epochs=epochs, balanced=True)
        return lambda Xq: predict(m, Xq)

    return fit


# ----------------------------------------------------- controlled curve behaviour
def test_curve_discrimination_decays_with_lead_time():
    """A signal that weakens with horizon must produce a decreasing AUC curve.

    POD is pinned near the target by construction (the threshold is chosen for
    it), so the horizon's *discriminative* skill shows up in AUC/FAR, not POD —
    that is exactly the quantity the curve must capture.
    """
    import random

    rng = random.Random(0)
    X, H = [], []
    for _ in range(1500):
        x = rng.random()
        X.append([x])
        near = 1 if x > 0.5 else 0
        far = 1 if (x > 0.5 and rng.random() > 0.5) else 0  # noisier => weaker signal
        H.append((near, far))
    split = 1000
    curve = lead_time_curve(
        X[:split], H[:split], X[split:], H[split:], [24, 72], _factory(), target_pod=0.9
    )
    aucs = {p.lead_hours: p.auc for p in curve}
    assert aucs[24] > aucs[72]


def test_actionable_lead_time_picks_longest_qualifying_horizon():
    from disastermind.ml.eval.leadtime import LeadPoint

    curve = [
        LeadPoint(24, 100, 20, 0.5, 0.95, 0.3, 0.9),
        LeadPoint(72, 100, 20, 0.5, 0.85, 0.4, 0.85),
        LeadPoint(120, 100, 20, 0.5, 0.60, 0.5, 0.7),
    ]
    assert actionable_lead_time(curve, min_pod=0.8) == 72
    assert actionable_lead_time(curve, min_pod=0.99) is None


def test_risk_trajectory_has_the_agreed_interface_shape():
    # two trivial detectors: 24h confident, 72h less so
    detectors = {
        24: lambda X: [0.9 for _ in X],
        72: lambda X: [0.6 for _ in X],
    }
    traj = risk_trajectory("guwahati", "2025-06-01T00:00:00Z", [1.0, 2.0], detectors, 0.5)
    assert traj["location_id"] == "guwahati"
    assert traj["threshold"] == 0.5
    assert [h["lead_hours"] for h in traj["horizons"]] == [24, 72]
    assert traj["horizons"][0]["p_event"] == 0.9
    # FAR is omitted when not supplied (consumer then applies no trust penalty)
    assert "far" not in traj["horizons"][0]


def test_risk_trajectory_carries_far_when_supplied():
    """The FAR contract extension: the live trajectory ships validated FAR/lead."""
    from disastermind.ml.eval.leadtime import LeadPoint, far_by_lead

    curve = [
        LeadPoint(24, 100, 20, 0.5, 0.9, 0.44, 0.98),
        LeadPoint(72, 100, 20, 0.5, 0.85, 0.72, 0.94),
    ]
    fmap = far_by_lead(curve)
    assert fmap == {24: 0.44, 72: 0.72}
    detectors = {24: lambda X: [0.9 for _ in X], 72: lambda X: [0.6 for _ in X]}
    traj = risk_trajectory("puri", "2025-06-01T00:00:00Z", [1.0], detectors, 0.5, fmap)
    # matches Session B's extended Horizon schema: per-horizon optional far float
    assert traj["horizons"][0]["far"] == 0.44
    assert traj["horizons"][1]["far"] == 0.72


# --------------------------------------------------------- real flood lead time
@pytest.mark.parametrize("h", F.HORIZONS)
def test_flood_rows_carry_per_horizon_labels(h):
    rows = F.load_rows()
    r = rows[0]
    assert len(r.horizon_labels) == len(F.HORIZONS)
    assert r.label_at(h) in (0, 1)


def test_flood_model_gives_multi_day_actionable_lead_time():
    rows = F.load_rows()
    tr, te = F.temporal_split(rows)
    step = max(1, len(tr) // 5000)
    tr = tr[::step]
    Xtr = [list(r.features) for r in tr]
    Htr = [r.horizon_labels for r in tr]
    Xte = [list(r.features) for r in te]
    Hte = [r.horizon_labels for r in te]
    curve = lead_time_curve(
        Xtr, Htr, Xte, Hte, [h * 24 for h in F.HORIZONS], _factory(40), target_pod=0.9
    )
    assert len(curve) >= 3
    # short lead must be at least as discriminating as long lead
    assert curve[0].auc >= curve[-1].auc
    # the model gives genuinely actionable warning — at least 2 days out at POD 80
    assert (actionable_lead_time(curve, min_pod=0.8) or 0) >= 48
    summary = to_dict(curve)
    assert summary["actionable_lead_hours_at_pod80"] is not None
