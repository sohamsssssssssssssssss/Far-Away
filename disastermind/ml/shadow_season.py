"""``python -m disastermind.ml.shadow_season`` — drive a real shadow-mode season.

Shadow mode is the institutional validation gate (see :mod:`disastermind.ml.shadow`):
the model predicts *live* but acts on *nothing*, every forecast is committed to a
tamper-evident hash-chained journal **before** its outcome can be known, and the
season is scored against reality and exported for independent review.

This module is the operator-facing CLI that makes that runnable today against
live feeds. The heavy lifting (the journal, scoring, export, chain verification)
already lives in :mod:`disastermind.ml.shadow`; this wires the prediction models
to it and gives a season a simple command surface:

  tick     — compute a live prediction for one hazard from a features file and
             append it to the journal (run this on a cron against your live feed).
  outcome  — once reality is known, attach the observed outcome by prediction id.
  score    — print the season scorecard (POD/FAR/AUC/Brier at the threshold).
  export   — write the full review packet (scorecard + every prediction) to JSON.
  verify   — re-verify the journal hash-chain (proves no record was edited).

Stdlib only. The features JSON is the integration seam: point any live adapter at
it (``disastermind.live`` can emit one per cycle) and the rest is deterministic.
See ``docs/SHADOW_SEASON.md`` for the end-to-end runbook.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from .registry import get_model
from .shadow import ShadowJournal, export_for_review, score_season

# Forecast horizon per hazard — how far ahead the prediction speaks, used to
# stamp ``window_end``. Earthquake is rapid impact assessment (no lead horizon).
_HORIZON_HOURS = {"cyclone": 168, "flood": 168, "earthquake": 0, "fire": 72}
# Hazard name -> Module value understood by the model registry (A/B/C).
_MODULE = {"cyclone": "A", "flood": "A", "earthquake": "B", "fire": "C"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_end(issued_at: str, hazard: str) -> str:
    base = datetime.fromisoformat(issued_at)
    return (base + timedelta(hours=_HORIZON_HOURS[hazard])).isoformat()


def _cmd_tick(args: argparse.Namespace) -> int:
    """Journal one live prediction from a features file."""
    feats = json.loads(open(args.features, encoding="utf-8").read())
    # Accept either a bare ordered list or a {name: value} mapping; the model's
    # feature order is the source of truth, so a mapping is sorted by key only as
    # a stable fallback — prefer an explicit ordered list from your adapter.
    if isinstance(feats, dict):
        values = [float(feats[k]) for k in sorted(feats)]
        feature_map = {k: float(v) for k, v in feats.items()}
    else:
        values = [float(x) for x in feats]
        feature_map = {f"f{i}": v for i, v in enumerate(values)}

    model = get_model(_MODULE[args.hazard])
    probability = float(model.predict_one(values))
    issued_at = _now_iso()
    journal = ShadowJournal(args.journal)
    rec = journal.record_prediction(
        args.id,
        hazard=args.hazard,
        issued_at=issued_at,
        window_end=_window_end(issued_at, args.hazard),
        probability=probability,
        threshold=args.threshold,
        features=feature_map,
        model_version=args.model_version,
    )
    out = {
        "journalled": rec.payload["id"],
        "hazard": args.hazard,
        "probability": round(probability, 4),
        "would_alert": rec.payload["would_alert"],
        "issued_at": issued_at,
        "chain_ok": journal.verify_chain(),
    }
    print(json.dumps(out, indent=2))
    return 0


def _cmd_outcome(args: argparse.Namespace) -> int:
    journal = ShadowJournal(args.journal)
    rec = journal.attach_outcome(
        args.id, occurred=args.occurred, observed_at=_now_iso(), detail=args.detail
    )
    print(json.dumps({"outcome_for": rec.payload["id"], "occurred": args.occurred,
                      "chain_ok": journal.verify_chain()}, indent=2))
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    print(json.dumps(score_season(ShadowJournal(args.journal)), indent=2))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    packet = export_for_review(ShadowJournal(args.journal))
    text = json.dumps(packet, indent=2)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        print(f"wrote review packet -> {args.out}")
    else:
        print(text)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    ok = ShadowJournal(args.journal).verify_chain()
    print(json.dumps({"journal": args.journal, "chain_intact": ok}, indent=2))
    return 0 if ok else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="disastermind.ml.shadow_season",
        description="Run a tamper-evident shadow-mode season against live feeds.",
    )
    p.add_argument("--journal", default="shadow_journal.jsonl",
                   help="path to the append-only journal (default: shadow_journal.jsonl)")
    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("tick", help="journal one live prediction from a features file")
    t.add_argument("--hazard", required=True, choices=sorted(_MODULE))
    t.add_argument("--features", required=True, help="JSON file: ordered list or {name: value}")
    t.add_argument("--id", required=True, help="unique prediction id (e.g. cell+timestamp)")
    t.add_argument("--threshold", type=float, default=0.5, help="alert threshold (default 0.5)")
    t.add_argument("--model-version", default="shadow", help="model version tag")
    t.set_defaults(func=_cmd_tick)

    o = sub.add_parser("outcome", help="attach the observed outcome for a prediction id")
    o.add_argument("--id", required=True)
    grp = o.add_mutually_exclusive_group(required=True)
    grp.add_argument("--occurred", dest="occurred", action="store_true")
    grp.add_argument("--not-occurred", dest="occurred", action="store_false")
    o.add_argument("--detail", default="")
    o.set_defaults(func=_cmd_outcome)

    s = sub.add_parser("score", help="print the season scorecard")
    s.set_defaults(func=_cmd_score)

    e = sub.add_parser("export", help="write the full review packet to JSON")
    e.add_argument("-o", "--out", default="", help="output path (default: stdout)")
    e.set_defaults(func=_cmd_export)

    v = sub.add_parser("verify", help="re-verify the journal hash-chain")
    v.set_defaults(func=_cmd_verify)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
