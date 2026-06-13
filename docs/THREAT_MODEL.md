# Threat model — the safety-critical decision path

> **Scope.** [`SECURITY.md`](../SECURITY.md) covers the *control plane* (API auth,
> CORS, rate limiting, audit integrity, container hardening). This document covers
> the **data plane**: the path from an external hazard signal to a recommended
> evacuation action. For a system whose outputs influence evacuation orders, a
> manipulated *input* is as dangerous as a compromised credential. This is the
> threat surface SECURITY.md gap #10 ("ingestion input is trusted-ish") defers —
> modelled here explicitly.

## The asset we are protecting

**Decision integrity**: that a recommended action reflects real hazard conditions,
not an attacker's fabricated ones. Two failure directions, both harmful:

- **False positive (induced over-warning):** an attacker fabricates a hazard to
  trigger an evacuation — causing panic, road casualties, resource waste, and
  cry-wolf erosion of future compliance.
- **False negative (induced suppression):** an attacker masks a real hazard so no
  warning is issued — the worst case, people not warned of a real event.

## Structural mitigation: bounded authority + human-in-the-loop

The single most important defense is architectural and already in place: **no
consequential action is autonomous.** The commander authority matrix
(`disastermind/tier1/commander/matrix.py`) requires human approval for mass
evacuations (> 10,000 people), cross-jurisdiction resources, military assets, and
media broadcast, and makes the highest-consequence actions human-only. A poisoned
feed therefore cannot, by itself, order a mass evacuation — it can at most produce
a *recommendation* a human reviews. The attacker must defeat both the data path
**and** a human commander. This is the property that makes the rest of the model a
defense-in-depth story rather than a single point of failure.

Every recommendation is also written to a hash-chained audit log
(`disastermind/audit/`), so a post-hoc "what drove this decision" review is
tamper-evident.

## Threats, by injection point

### T1 — Spoofed authoritative feed (USGS / IMD / GloFAS / FIRMS)
**Vector:** DNS/BGP hijack, compromised endpoint, or MITM injects fabricated
readings (a quake that didn't happen, a discharge spike).
**Today:** feeds are parsed defensively and the system degrades to in-process
fallbacks if a feed is unreachable, but **upstream payloads are not
signature-verified** (SECURITY.md gap #10). TLS to the provider is the only
integrity guarantee.
**Residual risk:** HIGH for false-positive injection on a single feed.
**Recommended:** (a) pin provider TLS / certificate where offered; (b) plausibility
bounds per feed (physical range + rate-of-change limits — reject a gauge that
jumps implausibly between polls); (c) **cross-source corroboration** before
activation (e.g. a quake should appear in both USGS and NCS; a flood should show
in discharge *and* rainfall). The model already consumes multiple physical drivers
per hazard — require ≥2 independent sources to agree before raising lead-time
alerts.

### T2 — Sensor / gauge spoofing (IoT telemetry)
**Vector:** a physical or networked sensor is compromised to report false
smoke/heat/water-level/structural readings.
**Today:** IoT gateways (`disastermind/tier3/iot/gateways.py`) ingest telemetry;
clustering exists but per-device authenticity is not cryptographically enforced.
**Residual risk:** MEDIUM (bounded by human-in-the-loop on consequential actions).
**Recommended:** per-device signing/keys; outlier rejection against neighbouring
sensors; a sensor that disagrees with its cluster is quarantined, not trusted.

### T3 — Adversarial social signal (NLP collapse-cluster intake)
**Vector:** a coordinated post flood fabricates a "building collapse" cluster to
trigger fire/collapse activation.
**Today — partially mitigated by design.** The social agent
(`disastermind/tier3/social/agent.py`) does **not** act on a single post: it
requires a **geo-clustered set of corroborating posts above a mean-confidence
threshold** before emitting even a `RAW_FEED` alert, and weights generic hazard
nouns lower so a few high-signal posts cannot dominate. A lone or low-confidence
poster is filtered.
**Residual risk:** MEDIUM — a *coordinated* bot campaign with geo-spoofed
locations could still manufacture a cluster.
**Recommended:** account-age/reputation weighting, per-account rate caps within a
cluster, and treating social signal as *corroborating-only* — never sufficient on
its own to drive a consequential recommendation (pair with T1 physical feeds).

### T4 — Suppression / denial (induced false negative)
**Vector:** DoS or selective blocking of a real feed so a genuine hazard is never
seen.
**Today:** graceful degradation keeps the system *running* without a feed, and the
feed-provenance panel surfaces real red/amber status rather than masking an outage
— so a missing feed is *visible*. But a silently *stale* (not absent) feed is
harder to catch.
**Residual risk:** MEDIUM.
**Recommended:** freshness/staleness watchdogs per feed (alert if last-good-read
exceeds an expected cadence); treat stale-but-present identically to absent.

### T5 — Model / threshold manipulation
**Vector:** tampering with model weights, the calibration artefact, or operating
thresholds to bias outputs.
**Today:** models are deterministic and committed; `make reproduce` regenerates
every published metric and **fails on drift**, so a silent change to model
behaviour is caught in CI. Thresholds are committed configuration.
**Residual risk:** LOW for the validated metrics; depends on artefact provenance.
**Recommended:** sign/checksum any trained model artefacts loaded at runtime;
keep operating thresholds in version control (they are) and review changes.

### T6 — Replay / stale-data injection
**Vector:** replaying a real past alert payload to trigger a stale activation.
**Today:** the shadow journal stamps `issued_at`/`window_end` and is hash-chained;
the live path does not yet enforce monotonic timestamps on ingestion.
**Residual risk:** LOW–MEDIUM.
**Recommended:** reject feed records whose timestamps are older than the last
accepted record for that source (monotonicity), and bound acceptable clock skew.

## Summary table

| ID | Threat | Direction | Today | Residual | Top mitigation |
|----|--------|-----------|-------|----------|----------------|
| T1 | Spoofed authoritative feed | false-pos | defensive parse, no sig | **HIGH** | cross-source corroboration + plausibility bounds |
| T2 | Sensor/gauge spoofing | both | clustering, no per-device auth | MED | device signing + neighbour outlier rejection |
| T3 | Adversarial social cluster | false-pos | **corroboration threshold (built)** | MED | corroborating-only + account weighting |
| T4 | Feed suppression / staleness | false-neg | degrades + visible status | MED | per-feed freshness watchdog |
| T5 | Model/threshold tamper | both | `make reproduce` drift gate | LOW | sign model artefacts |
| T6 | Replay / stale injection | false-pos | journal stamps only | LOW–MED | timestamp monotonicity on ingest |

## The one principle

**No single feed should be able to drive a consequential recommendation.** Two
layers already enforce a version of this — the social agent's corroboration
threshold and the commander's human-in-the-loop authority matrix. Extending
cross-source corroboration to the physical feeds (T1) is the highest-value
remaining hardening step on the safety-critical path.
