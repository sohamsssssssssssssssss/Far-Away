# Shadow-mode outreach — OSDMA / IIT Bhubaneswar

> **Local draft, not committed.** The single highest-leverage step for DisasterMind
> is non-code: get a real disaster-management agency to look at the Fani replay and
> agree to a *shadow* pilot (run in parallel, act on nothing, compare afterward).
> Odisha (OSDMA) is the natural first target — it is India's acknowledged leader in
> cyclone preparedness, and DisasterMind's strongest evidence is a leak-free replay
> of **Cyclone Fani (2019)**, an Odisha success story.

---

## Who to contact (in priority order)

1. **OSDMA** — Odisha State Disaster Management Authority (Bhubaneswar). The
   operational agency; most likely to value a shadow pilot. General contact via
   osdma.org; aim for the Early Warning / Forecasting cell.
2. **IIT Bhubaneswar — School of Earth, Ocean & Climate Sciences** / disaster
   research group. An academic partner lends credibility and can co-author the
   evaluation; lower barrier than a government agency for a first conversation.
3. **(Secondary)** IMD Regional Met Centre Bhubaneswar; NDMA (national, slower).

---

## The email (copy, adjust the salutation)

**Subject:** Shadow-mode pilot proposal — a validated multi-hazard early-warning
decision-support system, tested on Cyclone Fani

Dear [Name / OSDMA Early Warning Cell],

I'm Atharva Patil, an independent developer. I've built **DisasterMind**, a
multi-hazard (cyclone/flood/fire) early-warning *decision-support* system, and
I'd value 20 minutes of your time — not to sell or deploy anything, but to show
you one result and ask whether a shadow-mode pilot would interest you.

**The result:** I replayed **Cyclone Fani (2019)** through the system using *only*
the best-track data available before each forecast cutoff (strictly leak-free).
The system activated its cyclone response and produced an evacuation decision with
**multi-day lead time** — consistent with the real, world-class evacuation Odisha
executed that held Fani's toll to 64 deaths despite an Extremely Severe landfall.
I've since extended this to **92 real named cyclones (1990–2025, NOAA IBTrACS)**:
across the whole record, the system would have flagged a cyclone alert a **median
of ~54 hours before landfall**, and ≥48 h ahead for ~58% of storms.

**Why this is credible, not a black box:**
- The prediction models are validated on **real historical data**, leak-free, with
  proper out-of-sample splits and beating the operational baselines (persistence /
  climatology / standard fire-weather indices) with statistical significance.
- It is explicitly **decision-support**: every recommendation is for a human
  commander to accept or reject, with a tamper-evident audit trail of "what we
  knew, when, and what we recommended."
- I am candid about its limits (below) — I'd rather you trust the honest version.

**What I'm asking for:** a short conversation, and — if it's of interest — a
**shadow pilot**: run DisasterMind in parallel with your existing process for one
cyclone or monsoon season, acting on nothing, and compare its lead times and
recommendations against your actual decisions afterward. No operational risk, no
dependency, no cost to you.

**What would make it stronger (where you could help):** access to district-level
historical response records (evacuation timings, shelter capacities) to calibrate
the evacuation-planning layer against Odisha's real ground truth rather than my
current planning assumptions.

I can share a short technical brief, the Fani replay, and the multi-storm backtest
at your convenience.

With respect for the work your team does,
Atharva Patil
[email] · [phone] · [GitHub/demo link]

---

## Honest limitations to state up front (don't hide these)

- **Earthquakes are impact-triage, not forecasting** — no one can forecast quakes
  on an evacuation horizon; the cyclone/flood/fire forecasting is the real offer.
- **Evacuation-planning numbers (clearance time, compliance, casualties) are
  currently planning assumptions**, not calibrated to Odisha ground truth — that's
  exactly what a pilot + your data would fix.
- **It has never run live.** A shadow season is the first real test; that's the ask.

## Talking points if they engage

- Lead with the **Fani replay** (their success, their geography) — not the architecture.
- Frame it as *augmenting* their process (earlier, more structured, equity-aware
  warnings), never replacing their judgment or authority.
- Offer the **academic route** (IIT-BBS co-evaluation → a paper/case study) as a
  low-risk first step that de-risks a later operational pilot.
- The deliverable from a pilot is a **measured lead-time delta and false-alarm
  rate** vs their current decisions — a publishable, fundable result either way.

## Why Odisha specifically

Odisha turned itself into a global model for cyclone preparedness precisely by
being willing to experiment (post-1999 super-cyclone reforms, the zero-casualty
Phailin 2013 evacuation, the 1.2M+ Fani evacuation). An agency with that culture
is the most likely in India to say yes to a no-risk shadow pilot.
