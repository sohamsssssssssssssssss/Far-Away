# Running a real shadow-mode season

> **Why this is the decisive step.** Statistical validation on historical fixtures
> proves the model *could* have worked. A shadow season proves it works *now*, on
> live data it has never seen, with a tamper-evident record no one can curate
> after the fact. This is the artefact to hand a disaster-management authority.
> The system predicts live and **acts on nothing**; only the journal accumulates.

The harness already exists (`disastermind/ml/shadow.py` — append-only,
hash-chained journal, scoring, review export) and is driven by the
`disastermind.ml.shadow_season` CLI. This runbook is how to actually run a season.

## What a season produces

A single JSONL journal where **every prediction is written before its outcome can
be known**, each line hash-chained to the last. Any post-hoc edit breaks the chain
and is detected (`verify`). At the end you export one packet containing *every*
prediction — hits, misses, and unresolved — so the record cannot be cherry-picked.

## The integration seam

`tick` reads a **features file** — an ordered JSON list (preferred) or a
`{name: value}` map — and journals the model's probability for it. Point any live
adapter at that file. The keyless feeds wired today (USGS/NCS seismology,
Open-Meteo weather/GloFAS, NASA FIRMS) can each emit one per cycle; the rest need
provider keys (see `.env.example`). Producing the features file is the only
integration work; everything downstream is deterministic and offline.

## Day-to-day commands

```bash
# 1. On a cron (hourly/daily per hazard), dump current live features to JSON,
#    then journal the prediction. Use a unique id per (cell, time).
python -m disastermind.ml.shadow_season --journal season.jsonl \
    tick --hazard flood --features /run/dm/flood_basin12.json \
    --id flood-basin12-2026-06-13T12 --threshold 0.5

# 2. When reality is known (gauge exceeded? fire detected? quake felt?),
#    attach the outcome by id. Predictions are never mutated.
python -m disastermind.ml.shadow_season --journal season.jsonl \
    outcome --id flood-basin12-2026-06-13T12 --occurred --detail "GloFAS Q>Q10"
#    ...or --not-occurred for a confirmed non-event.

# 3. Anytime: prove nothing was edited, and read the running scorecard.
python -m disastermind.ml.shadow_season --journal season.jsonl verify
python -m disastermind.ml.shadow_season --journal season.jsonl score

# 4. End of season: export the full review packet for independent review.
python -m disastermind.ml.shadow_season --journal season.jsonl export -o review_packet.json
```

## Suggested protocol (defensible to a reviewer)

1. **Pre-register** the threshold and the outcome definition per hazard *before*
   the season starts (commit them) — so the operating point isn't chosen to flatter
   the result. The CLI's `--threshold` is the pre-registered value.
2. **Run ≥ one full season** per hazard (a monsoon for flood, a fire season for
   fire). Cron `tick`; never skip a cycle — gaps look like cherry-picking.
3. **Resolve every prediction.** `score` reports `n_unresolved`; drive it to zero
   before claiming a result. Unresolved predictions stay in the export.
4. **Verify the chain** on every read and in the final packet (`chain_verified`).
5. **Hand the packet to an external reviewer.** It contains the scorecard *and*
   every individual prediction — the misses are not removable.

## It is already running (live USGS earthquake season)

The "wire a keyless feed and start the cron" step is **done** for earthquakes:

- `disastermind/live/usgs_shadow.py` pulls the public USGS M4.5+ feed, journals a
  leak-free impact prediction per new event with the **same validated logistic**
  behind the published 0.937 AUC, and settles real outcomes after a 2-day window.
- The `shadow-season` GitHub workflow runs it daily and commits the growing
  journal to `shadow/usgs_season.jsonl` (seeded on first run with live events).
- Drive it locally with `make shadow-tick` / `make shadow-score` /
  `make shadow-verify`; tested offline in `tests/test_usgs_shadow.py`.

To add **flood** or **fire** seasons, point their adapters at the features file as
described above — the same journal/scoring/export machinery applies.

## Status

- **Running now:** the live USGS earthquake season (above) — predictions accruing,
  outcomes settling on the 2-day window, hash-chain verified each run.
- **Ready to wire:** flood (Open-Meteo/GloFAS) and fire (FIRMS) seasons via the
  features-file seam.
- **The payoff:** a 30–60 day journal against live data is a credential almost no
  comparable project can show — and it now accumulates on its own.
