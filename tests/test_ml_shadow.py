"""Shadow mode: hash-chained journal, season scoring, review export."""
from __future__ import annotations

import json

import pytest

from disastermind.ml.shadow import ShadowJournal, export_for_review, score_season


def _seeded_journal(tmp_path) -> ShadowJournal:
    j = ShadowJournal(str(tmp_path / "season.jsonl"))
    for i, (p, occurred) in enumerate(
        [(0.9, True), (0.8, True), (0.7, False), (0.2, False), (0.1, False), (0.15, True)]
    ):
        j.record_prediction(
            f"evt-{i}",
            hazard="flood",
            issued_at=f"2025-06-{i + 1:02d}T00:00:00Z",
            window_end=f"2025-06-{i + 4:02d}T00:00:00Z",
            probability=p,
            threshold=0.5,
            model_version="m1",
        )
        if i < 5:  # leave one unresolved
            j.attach_outcome(
                f"evt-{i}", occurred=occurred, observed_at=f"2025-06-{i + 4:02d}T01:00:00Z"
            )
    return j


def test_chain_verifies_and_scorecard_joins_predictions_to_outcomes(tmp_path):
    j = _seeded_journal(tmp_path)
    assert j.verify_chain()
    card = score_season(j)
    assert card["n_predictions"] == 6
    assert card["n_resolved"] == 5
    assert card["unresolved_ids"] == ["evt-5"]  # visible, not dropped
    assert card["confusion"]["tp"] == 2 and card["confusion"]["fn"] == 0
    assert card["auc"] > 0.5
    assert 0 <= card["brier"] <= 1


def test_tampering_breaks_the_chain_and_blocks_scoring(tmp_path):
    j = _seeded_journal(tmp_path)
    lines = open(j.path, encoding="utf-8").read().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["probability"] = 0.01  # rewrite history
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    open(j.path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    assert not j.verify_chain()
    with pytest.raises(ValueError):
        score_season(j)


def test_predictions_are_committed_before_outcomes(tmp_path):
    j = _seeded_journal(tmp_path)
    kinds = [r.kind for r in j.records()]
    for pid in ("evt-0", "evt-1"):
        pred_pos = next(
            i for i, r in enumerate(j.records())
            if r.kind == "prediction" and r.payload["id"] == pid
        )
        out_pos = next(
            i for i, r in enumerate(j.records())
            if r.kind == "outcome" and r.payload["id"] == pid
        )
        assert pred_pos < out_pos
    assert kinds.count("prediction") == 6


def test_export_for_review_carries_everything(tmp_path):
    j = _seeded_journal(tmp_path)
    export = export_for_review(j)
    assert export["scorecard"]["n_predictions"] == 6
    assert len(export["journal"]) == 11  # 6 predictions + 5 outcomes, nothing pruned
    # a reviewer can recompute the metrics from the export alone
    payloads = [r["payload"] for r in export["journal"] if r["kind"] == "prediction"]
    assert all("probability" in p and "issued_at" in p for p in payloads)


def test_empty_journal_scores_to_zero_counts(tmp_path):
    j = ShadowJournal(str(tmp_path / "empty.jsonl"))
    card = score_season(j)
    assert card["n_predictions"] == 0 and card["n_resolved"] == 0
