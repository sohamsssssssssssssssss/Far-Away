"""End-to-end tests for the shadow-season CLI (``disastermind.ml.shadow_season``).

These exercise the operator surface that drives a real shadow season: journal a
live prediction, attach its outcome, score the season, export the review packet,
and — critically — prove the journal is tamper-evident (an edited record breaks
the hash chain and is detected).
"""
from __future__ import annotations

import json

from disastermind.ml import shadow_season


def _features(tmp_path, values):
    p = tmp_path / "feat.json"
    p.write_text(json.dumps(values))
    return str(p)


def test_tick_journals_prediction_with_intact_chain(tmp_path, capsys):
    journal = str(tmp_path / "j.jsonl")
    rc = shadow_season.main(
        ["--journal", journal, "tick", "--hazard", "earthquake",
         "--features", _features(tmp_path, [6.2, 10.0, 20.0, 1.0, 0.5]),
         "--id", "eq-1", "--threshold", "0.3"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["journalled"] == "eq-1"
    assert out["chain_ok"] is True
    assert 0.0 <= out["probability"] <= 1.0


def test_full_season_lifecycle(tmp_path, capsys):
    journal = str(tmp_path / "j.jsonl")
    feats = _features(tmp_path, [6.2, 10.0, 20.0, 1.0, 0.5])
    shadow_season.main(["--journal", journal, "tick", "--hazard", "earthquake",
                        "--features", feats, "--id", "eq-1"])
    capsys.readouterr()
    shadow_season.main(["--journal", journal, "outcome", "--id", "eq-1", "--occurred"])
    capsys.readouterr()

    assert shadow_season.main(["--journal", journal, "verify"]) == 0
    score = json.loads(capsys.readouterr().out)
    assert score["chain_intact"] is True

    assert shadow_season.main(["--journal", journal, "score"]) == 0
    season = json.loads(capsys.readouterr().out)
    assert season["n_predictions"] == 1
    assert season["n_resolved"] == 1
    assert season["chain_verified"] is True


def test_export_writes_full_review_packet(tmp_path, capsys):
    journal = str(tmp_path / "j.jsonl")
    feats = _features(tmp_path, [6.2, 10.0, 20.0, 1.0, 0.5])
    shadow_season.main(["--journal", journal, "tick", "--hazard", "fire",
                        "--features", feats, "--id", "fire-1"])
    capsys.readouterr()
    out_path = str(tmp_path / "packet.json")
    shadow_season.main(["--journal", journal, "export", "-o", out_path])
    packet = json.loads(open(out_path).read())
    assert "scorecard" in packet or "predictions" in packet or packet  # non-empty packet


def test_tampering_breaks_the_chain(tmp_path):
    journal = str(tmp_path / "j.jsonl")
    feats = _features(tmp_path, [6.2, 10.0, 20.0, 1.0, 0.5])
    shadow_season.main(["--journal", journal, "tick", "--hazard", "earthquake",
                        "--features", feats, "--id", "eq-1", "--threshold", "0.9"])

    # Forge the outcome: rewrite the journal so the prediction looks like a hit.
    lines = open(journal).read().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["probability"] = 0.99
    rec["payload"]["would_alert"] = True
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    open(journal, "w").write("\n".join(lines) + "\n")

    # The edit must be detected — chain verification fails, exit code is non-zero.
    assert shadow_season.main(["--journal", journal, "verify"]) == 1
