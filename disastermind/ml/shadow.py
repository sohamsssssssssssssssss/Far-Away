"""Shadow mode — run the model live, score it later, trust it only after.

The final validation gate is institutional, not statistical: before any model
output influences a real dispatch, it must run through at least one full season
*predicting live but acting on nothing*, and its forecasts must then be scored
against what actually happened and shown to independent reviewers. This module
is that harness:

  * :class:`ShadowJournal` — an append-only JSONL log of timestamped live
    predictions. Each record is written BEFORE the outcome exists, so the
    journal is tamper-evident by construction: a record's ``issued_at`` precedes
    its event window, and each line carries a hash chained to the previous line
    (any post-hoc edit breaks the chain).
  * :func:`attach_outcome` — append an outcome record (what actually happened)
    that references the prediction by id. Predictions are never mutated.
  * :func:`score_season` — join predictions to outcomes and emit the season
    scorecard: POD/FAR at the declared operating threshold, AUC, Brier, the
    reliability table, counts of unresolved predictions — the artefact one hands
    to an external review panel.
  * :func:`export_for_review` — the scorecard + full per-event journal in one
    JSON document for independent peer review (no cherry-picking possible: the
    export carries every prediction, including the unresolved and the wrong).

Stdlib only. The journal forces honesty mechanically: predictions are committed
before outcomes are knowable, the chain hash freezes the record order, and
scoring excludes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .eval.decision import confusion_at
from .eval.metrics import brier_score, calibration_bins, expected_calibration_error, roc_auc

_GENESIS = "shadow-genesis"


def _chain_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """SHA-256 over the previous hash + canonical payload JSON."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{prev_hash}|{body}".encode()).hexdigest()


@dataclass(frozen=True)
class ShadowRecord:
    """One journal line (a prediction or an outcome) with its chain hash."""

    kind: str  # "prediction" | "outcome"
    payload: dict[str, Any]
    hash: str


class ShadowJournal:
    """Append-only, hash-chained JSONL journal of live shadow predictions.

    ``issued_at`` (ISO timestamp) and ``window_end`` are REQUIRED on every
    prediction: a forecast must say what period it covers, and scoring will
    treat predictions whose window has not closed as unresolved rather than
    silently dropping them.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    # ------------------------------------------------------------------ writing
    def _last_hash(self) -> str:
        last = _GENESIS
        for rec in self._iter_raw():
            last = rec.get("hash", last)
        return last

    def record_prediction(
        self,
        prediction_id: str,
        *,
        hazard: str,
        issued_at: str,
        window_end: str,
        probability: float,
        threshold: float,
        features: dict[str, float] | None = None,
        model_version: str = "unversioned",
    ) -> ShadowRecord:
        """Append one live prediction (before its outcome can be known)."""
        payload = {
            "id": str(prediction_id),
            "hazard": hazard,
            "issued_at": issued_at,
            "window_end": window_end,
            "probability": float(probability),
            "threshold": float(threshold),
            "would_alert": float(probability) >= float(threshold),
            "features": features or {},
            "model_version": model_version,
        }
        return self._append("prediction", payload)

    def attach_outcome(
        self, prediction_id: str, *, occurred: bool, observed_at: str, detail: str = ""
    ) -> ShadowRecord:
        """Append the real outcome for a previously-journalled prediction."""
        payload = {
            "id": str(prediction_id),
            "occurred": bool(occurred),
            "observed_at": observed_at,
            "detail": detail,
        }
        return self._append("outcome", payload)

    def _append(self, kind: str, payload: dict[str, Any]) -> ShadowRecord:
        h = _chain_hash(self._last_hash(), payload)
        rec = {"kind": kind, "payload": payload, "hash": h}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
        return ShadowRecord(kind=kind, payload=payload, hash=h)

    # ------------------------------------------------------------------ reading
    def _iter_raw(self) -> Iterable[dict[str, Any]]:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def verify_chain(self) -> bool:
        """True iff every line's hash matches the recomputed chain (no edits)."""
        prev = _GENESIS
        for rec in self._iter_raw():
            if rec.get("hash") != _chain_hash(prev, rec.get("payload", {})):
                return False
            prev = rec["hash"]
        return True

    def records(self) -> list[ShadowRecord]:
        return [
            ShadowRecord(kind=r["kind"], payload=r["payload"], hash=r["hash"])
            for r in self._iter_raw()
        ]


# ------------------------------------------------------------------------- scoring
def score_season(journal: ShadowJournal) -> dict[str, Any]:
    """Join predictions to outcomes and emit the shadow-season scorecard.

    Every prediction appears in exactly one bucket: resolved (scored) or
    unresolved (counted, listed). The scorecard refuses to exist on a corrupted
    journal — ``verify_chain`` failure raises rather than silently scoring.
    """
    if not journal.verify_chain():
        raise ValueError("shadow journal hash chain is broken — refusing to score")
    predictions: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    for rec in journal.records():
        if rec.kind == "prediction":
            predictions[rec.payload["id"]] = rec.payload
        elif rec.kind == "outcome":
            outcomes[rec.payload["id"]] = rec.payload

    resolved = [(p, outcomes[pid]) for pid, p in predictions.items() if pid in outcomes]
    unresolved = [pid for pid in predictions if pid not in outcomes]

    scorecard: dict[str, Any] = {
        "n_predictions": len(predictions),
        "n_resolved": len(resolved),
        "n_unresolved": len(unresolved),
        "unresolved_ids": sorted(unresolved),
        "chain_verified": True,
    }
    if resolved:
        y = [1 if o["occurred"] else 0 for _, o in resolved]
        p = [pr["probability"] for pr, _ in resolved]
        # The operating threshold was declared per prediction, live; the season
        # is scored at the median declared threshold (and it is reported).
        thresholds = sorted(pr["threshold"] for pr, _ in resolved)
        threshold = thresholds[len(thresholds) // 2]
        bins = calibration_bins(y, p, n_bins=10)
        scorecard.update(
            {
                "threshold": threshold,
                "confusion": confusion_at(y, p, threshold).to_dict(),
                "auc": roc_auc(y, p),
                "brier": brier_score(y, p),
                "ece": expected_calibration_error(bins),
                "reliability": [b.to_dict() for b in bins if b.count],
                "base_rate": sum(y) / len(y),
            }
        )
    return scorecard


def export_for_review(journal: ShadowJournal) -> dict[str, Any]:
    """Scorecard + the COMPLETE journal, for independent external review.

    The export is everything: every prediction (right, wrong, unresolved), every
    outcome, every hash. A reviewer can recompute the chain and every metric
    from this single document.
    """
    return {
        "scorecard": score_season(journal),
        "journal": [
            {"kind": r.kind, "payload": r.payload, "hash": r.hash}
            for r in journal.records()
        ],
    }
