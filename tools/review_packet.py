#!/usr/bin/env python3
"""Assemble a self-contained external-review packet.

An independent review is worth more than thousands more lines of code — but a
reviewer will only engage if the ask is concrete and everything they need is in
one place. This tool builds that package: it regenerates the full validation
evidence and bundles it with the technical report, the limitations, the threat
model, and the reviewer brief into a single timestamped directory a domain expert
can open and assess without running anything.

What it collects:
  * validation_report.json  — the complete machine-readable validation output
                              (every metric behind the published tables)
  * validation_summary.txt  — the human-readable claimed-vs-reproduced check
  * TECHNICAL_REPORT.md      — the write-up incl. the failure analysis
  * THREAT_MODEL.md          — the safety-critical data-path threat model
  * EVAC_CALIBRATION.md      — the evacuation-calibration protocol
  * SHADOW_SEASON.md         — the live-validation runbook
  * EXTERNAL_REVIEW.md       — the reviewer brief (the concrete ask)
  * MANIFEST.json            — what's in the packet + how to reproduce it

Offline, stdlib-only. Usage:
    python tools/review_packet.py [--out DIR]

Note: this script avoids wall-clock calls (deterministic by design); pass --stamp
to label the packet directory, otherwise it is named 'review_packet'.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (source path relative to repo root, destination filename in the packet)
DOCS = [
    ("docs/TECHNICAL_REPORT.md", "TECHNICAL_REPORT.md"),
    ("docs/THREAT_MODEL.md", "THREAT_MODEL.md"),
    ("docs/EVAC_CALIBRATION.md", "EVAC_CALIBRATION.md"),
    ("docs/SHADOW_SEASON.md", "SHADOW_SEASON.md"),
    ("docs/EXTERNAL_REVIEW.md", "EXTERNAL_REVIEW.md"),
    ("docs/validation_golden.json", "validation_golden.json"),
    ("PROJECT_OVERVIEW.md", "PROJECT_OVERVIEW.md"),
]


def _run(args: list[str]) -> str:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args)}")
    return proc.stdout


def build(out_dir: Path) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # 1. Regenerate the full validation evidence from raw fixtures.
    print("· regenerating validation report (offline)…")
    report_json = _run([sys.executable, "-m", "disastermind.ml.validation", "--json"])
    (out_dir / "validation_report.json").write_text(report_json)

    # 2. The human-readable reproducibility check.
    print("· running reproducibility check…")
    summary = _run([sys.executable, "tools/reproduce.py"])
    (out_dir / "validation_summary.txt").write_text(summary)

    # 3. Copy the narrative docs.
    copied = []
    for src, dst in DOCS:
        sp = ROOT / src
        if sp.exists():
            shutil.copy2(sp, out_dir / dst)
            copied.append(dst)
        else:
            print(f"  ! missing (skipped): {src}")

    # 4. Manifest.
    hazards = json.loads(report_json).get("hazards", {})
    manifest = {
        "packet": "DisasterMind external-review packet",
        "reproduce_command": "make reproduce",
        "regenerate_packet": "python tools/review_packet.py",
        "contents": ["validation_report.json", "validation_summary.txt", *copied],
        "hazards_covered": sorted(hazards.keys()),
        "headline_metrics": {
            h: {k: v.get("model", {}).get(k) for k in ("auc", "brier", "ece")}
            for h, v in hazards.items()
        },
        "note": "All numbers regenerate offline from committed fixtures; the "
                "validation suite excludes nothing and the shadow journal is "
                "tamper-evident. See EXTERNAL_REVIEW.md for the review ask.",
    }
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the external-review packet.")
    ap.add_argument("--out", default="review_packet", help="output directory")
    args = ap.parse_args()

    out_dir = (ROOT / args.out).resolve()
    manifest = build(out_dir)
    print("\n" + "=" * 70)
    print(f"Review packet written to: {out_dir}")
    print(f"  {len(manifest['contents'])} files · hazards: "
          f"{', '.join(manifest['hazards_covered'])}")
    print("  Hand the directory to a reviewer; everything reproduces with "
          "`make reproduce`.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
