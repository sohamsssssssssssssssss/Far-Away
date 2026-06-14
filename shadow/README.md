# Live shadow season — append-only journal

This directory holds a **real, running shadow-mode season**, not a fixture.

`usgs_season.jsonl` is an append-only, hash-chained journal of live earthquake
impact predictions. Every weekday the `shadow-season` GitHub workflow pulls the
public [USGS M4.5+ feed](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson),
journals a **leak-free** damaging-impact prediction for each new event the moment
it is detected (using only pre-outcome physical inputs — magnitude, depth,
location), and — after a 2-day settle window — attaches the **real outcome** once
USGS finalises the event's ShakeMap/PAGER alert. Predictions are never mutated;
each line is hash-chained to the previous one, so any post-hoc edit breaks the
chain (`make shadow-verify`).

The model is the *same* validated logistic behind the published 0.937 AUC
(`disastermind.ml.validation.run.quake_spec`), fit on the committed 2013–2017
training split — so this season shadows the validated model, not a different one.

## Why this matters

Statistical validation on historical fixtures shows the model *could* have worked.
This journal shows it working **now**, on live data it has never seen, with a
tamper-evident record no one can curate after the fact. The system predicts and
acts on nothing — only the journal accumulates.

## Read the running scorecard

```bash
make shadow-score                       # POD/FAR/AUC/Brier once outcomes accrue
make shadow-verify                      # prove the hash-chain is intact
python -m disastermind.ml.shadow_season --journal shadow/usgs_season.jsonl export -o review_packet.json
```

Early on, most predictions are **unresolved** (the 2-day settle window hasn't
elapsed); the scorecard reports `n_unresolved` honestly and the export carries
every prediction — hits, misses, and pending alike. Nothing is cherry-picked.

Seeded on first run with the live feed; it grows by itself on the schedule.
