"""Tests for the live USGS shadow-season feeder (`disastermind.live.usgs_shadow`).

Network is monkeypatched out: `_fetch_json` is replaced with canned, real-shaped
USGS GeoJSON so the whole tick → resolve → verify loop runs offline and
deterministically. The model itself is the real validated logistic (fit on the
committed training fixture), so these also confirm the live path produces sane,
in-range probabilities from genuine physical inputs.
"""
from __future__ import annotations

import json

from disastermind.live import usgs_shadow as U
from disastermind.ml.shadow import ShadowJournal


def _feature(eid, mag, depth, lat=35.0, lon=140.0, alert=None, mmi=0.0, felt=0, t=1_700_000_000_000):
    return {
        "id": eid,
        "properties": {"mag": mag, "alert": alert, "mmi": mmi, "felt": felt,
                       "tsunami": 0, "time": t},
        "geometry": {"coordinates": [lon, lat, depth]},
    }


def _feed(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


def test_tick_journals_real_model_predictions(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_fetch_json", lambda *a, **k: _feed(
        _feature("eqA", 5.1, 10.0), _feature("eqB", 4.6, 220.0)))
    j = ShadowJournal(str(tmp_path / "s.jsonl"))
    added = U.tick(j, "https://x")
    assert added == 2
    recs = [r for r in j.records() if r.kind == "prediction"]
    assert {r.payload["id"] for r in recs} == {"eqA", "eqB"}
    for r in recs:
        assert 0.0 <= r.payload["probability"] <= 1.0
        assert r.payload["model_version"] == U._MODEL_VERSION
    # A shallow M5.1 must score higher damaging-risk than a deep (220km) M4.6.
    by_id = {r.payload["id"]: r.payload["probability"] for r in recs}
    assert by_id["eqA"] > by_id["eqB"]
    assert j.verify_chain()


def test_tick_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_fetch_json", lambda *a, **k: _feed(_feature("eqA", 5.0, 12.0)))
    j = ShadowJournal(str(tmp_path / "s.jsonl"))
    assert U.tick(j, "https://x") == 1
    assert U.tick(j, "https://x") == 0  # same event not double-journalled


def test_resolve_attaches_real_outcome_after_grace(tmp_path, monkeypatch):
    issued_t = 1_700_000_000_000
    monkeypatch.setattr(U, "_fetch_json", lambda *a, **k: _feed(
        _feature("eqA", 5.5, 15.0, t=issued_t)))
    j = ShadowJournal(str(tmp_path / "s.jsonl"))
    U.tick(j, "https://x")

    # Now the event has settled to a PAGER yellow alert -> damaging outcome.
    monkeypatch.setattr(U, "_fetch_json",
                        lambda *a, **k: _feature("eqA", 5.5, 15.0, alert="yellow", t=issued_t))
    later = issued_t + U.RESOLVE_GRACE_MS + 1
    assert U.resolve(j, now_ms=later) == 1
    outcomes = [r for r in j.records() if r.kind == "outcome"]
    assert outcomes and outcomes[0].payload["id"] == "eqA"
    assert outcomes[0].payload["occurred"] is True
    assert j.verify_chain()


def test_resolve_waits_for_grace_period(tmp_path, monkeypatch):
    issued_t = 1_700_000_000_000
    monkeypatch.setattr(U, "_fetch_json", lambda *a, **k: _feed(_feature("eqA", 5.0, 20.0, t=issued_t)))
    j = ShadowJournal(str(tmp_path / "s.jsonl"))
    U.tick(j, "https://x")
    # Only 1 hour later — inside the grace window, nothing resolves yet.
    assert U.resolve(j, now_ms=issued_t + 3_600_000) == 0


def test_network_failure_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_fetch_json", lambda *a, **k: None)
    j = ShadowJournal(str(tmp_path / "s.jsonl"))
    assert U.tick(j, "https://x") == 0  # no crash, nothing journalled
    assert j.verify_chain()


def test_malformed_features_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_fetch_json", lambda *a, **k: _feed(
        {"id": "bad1", "properties": {}, "geometry": {"coordinates": [1, 2, 3]}},  # no mag
        {"id": "bad2", "properties": {"mag": 5.0}, "geometry": {"coordinates": [1, 2]}},  # no depth
        _feature("good", 5.0, 10.0)))
    j = ShadowJournal(str(tmp_path / "s.jsonl"))
    assert U.tick(j, "https://x") == 1  # only the well-formed one


def test_cli_main_runs_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(U, "_fetch_json", lambda *a, **k: _feed(_feature("eqA", 5.0, 10.0)))
    rc = U.main(["--journal", str(tmp_path / "s.jsonl"), "--mode", "tick"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["predictions_added"] == 1
    assert out["chain_intact"] is True
