# External review — the ask

> **Why this is high-leverage.** One domain expert reading this work and giving a
> critique (or a quote) is worth more than thousands more lines of code: it is
> real-world validation of *the project*, not just the model. This document makes
> the ask concrete enough that a busy expert can engage in an afternoon, and
> `tools/review_packet.py` bundles everything they need into one directory.

## Who to ask (any one is enough to start)

- An academic **hydrologist / flood-forecasting** researcher (for the flood model
  and the lead-time-vs-skill framing).
- A **seismologist / earthquake-engineering** academic (for the rapid-impact
  assessment framing and the GMPE/PAGER baseline comparison).
- A **disaster-management practitioner** — a state DMA (e.g. OSDMA) engineer, or a
  disaster-NGO technical lead (for the evacuation-decision layer and operational
  realism).
- A **fire-danger / remote-sensing** researcher (for the FIRMS/FPA-FOD fire models).

## What to send

Run:

```bash
python tools/review_packet.py        # writes ./review_packet/
```

The packet is self-contained and regenerates from committed fixtures:

| File | What it is |
|---|---|
| `TECHNICAL_REPORT.md` | The full evaluation, including the failure analysis (§6) |
| `validation_report.json` | Every metric behind the published tables (machine-readable) |
| `validation_summary.txt` | The claimed-vs-reproduced check (all Δ = 0.0000) |
| `THREAT_MODEL.md` | Safety-critical data-path threat model |
| `EVAC_CALIBRATION.md` | The evacuation-calibration protocol |
| `SHADOW_SEASON.md` | The live-validation runbook |
| `PROJECT_OVERVIEW.md` | System and product overview |
| `MANIFEST.json` | Contents + how to reproduce |

## The five questions to put to the reviewer

Keep the ask small and specific — these are the load-bearing claims:

1. **Protocol.** Is the leak-free evaluation protocol (temporal splits; thresholds
   and calibrators fit on a calibration split, never on test; paired-bootstrap
   significance vs. the operational incumbent) sound? Any leakage path missed?

2. **Baselines.** Are the incumbents fair — GMPE attenuation and PAGER for
   earthquakes, persistence and seasonal climatology for floods, the Ångström
   index for fire? Is there a stronger baseline a practitioner would actually use?

3. **Labels.** Are the proxy outcome labels (discharge exceedance, FIRMS
   detections, instrumental intensity) defensible stand-ins for real impact, or do
   they bias the result in a way the report doesn't acknowledge?

4. **Operational realism.** At the dispatch thresholds, are the POD/FAR trade-offs
   (and the cry-wolf framing) realistic for a real warning operation? Is the
   evacuation-decision layer's structure credible pending calibration?

5. **The honest gaps.** Does the failure analysis (§6) miss any material weakness?
   What is the single thing you'd require before trusting an output operationally?

## What to do with the response

- Capture the reviewer's name, affiliation, date, and verbatim critique/quote in
  `docs/reviews/` (create it), one file per reviewer.
- Turn each substantive critique into a tracked issue; fix or explicitly accept it.
- If the reviewer is willing, a short attributed quote in the README is strong
  third-party signal. Only use attributed quotes with explicit permission.

## Status

- **Ready now:** the packet generator and this brief. The packet reproduces every
  number offline, so a reviewer can verify rather than trust.
- **Your move:** send it to one expert from the list above. A single documented,
  attributed review materially changes how this project is read.
