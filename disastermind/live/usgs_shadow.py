"""Drive a REAL shadow season from the live USGS earthquake feed (keyless).

This turns the shadow-mode harness (:mod:`disastermind.ml.shadow`) from
"execute-ready" into "executing": on a schedule it pulls the live USGS feed,
journals a *leak-free* impact prediction for each new M4.5+ event the instant it
is detected (using only pre-outcome physical inputs — magnitude, depth,
location), and later attaches the real outcome once USGS settles the event's
ShakeMap/PAGER alert. The journal is append-only and hash-chained, so the
accumulating record is tamper-evident by construction.

Crucially, the prediction comes from the **same** deterministic logistic that
produced the published validation metrics: it is fit here on the committed
training split (``load_quakes`` → ``temporal_split`` → ``fit_logistic`` with the
damaging-outcome label and ``balanced=True``), exactly as in
``disastermind.ml.validation.run.quake_spec``. The live season therefore shadows
the validated model, not a different in-process one.

Two modes (the scheduled job runs both):
  * ``tick``    — journal a prediction for every new event in the feed window.
  * ``resolve`` — for journalled predictions older than a grace period and not
                  yet resolved, fetch the event's final status and attach the
                  real damaging/non-damaging outcome.

Stdlib only (``urllib`` for the fetch). Network failures are handled gracefully:
a transient outage logs and exits 0 (nothing to commit), never corrupting the
journal. See ``docs/SHADOW_SEASON.md`` and the ``shadow-season`` GitHub workflow.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence

from ..ml.shadow import ShadowJournal
from ..ml.validation.dataset import Quake, load_quakes, temporal_split
from ..ml.validation.run import fit_logistic, predict

# M4.5+ events in the past day — keyless, public, stable GeoJSON.
DEFAULT_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
# Per-event detail endpoint, used to settle outcomes during `resolve`.
EVENT_DETAIL = "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&eventid={eid}"
# Validated operating threshold (chosen on the calibration split for target
# POD 0.9 in quake_spec); recorded so `would_alert` and scoring are meaningful.
OPERATING_THRESHOLD = 0.0297
# Wait this long after issuing before an outcome is considered settled.
RESOLVE_GRACE_MS = 2 * 24 * 60 * 60 * 1000  # 2 days
_MODEL_VERSION = "earthquake-damaging/logistic-balanced/v1"

_FIT = None  # lazily-fit validated model (deterministic; one fit per process)


def _fitted_model():
    global _FIT
    if _FIT is None:
        train, _ = temporal_split(load_quakes())
        _FIT = fit_logistic(
            [q.features() for q in train],
            [q.label_damaging() for q in train],
            name="earthquake-damaging",
            balanced=True,
        )
    return _FIT


def _ssl_context():
    """Default SSL context, falling back to certifi's CA bundle if the system
    store is missing (a common bare-Python situation). certifi is optional —
    stdlib-first; CI runners have a working system store regardless."""
    import ssl
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _fetch_json(url: str, *, timeout: float = 20.0) -> dict | None:
    """GET a JSON document; return None (logged) on any network/parse failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "DisasterMind-Shadow/1.0"})
    context = _ssl_context() if url.startswith("https") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"[shadow] feed unreachable ({url.split('?')[0]}): {exc}", file=sys.stderr)
        return None


def _quake_from_feature(feat: dict) -> tuple[str, Quake] | None:
    """Map a USGS GeoJSON feature to (event_id, Quake); None if unusable."""
    eid = feat.get("id")
    props = feat.get("properties") or {}
    geom = (feat.get("geometry") or {}).get("coordinates") or []
    if not eid or props.get("mag") is None or len(geom) < 3 or geom[2] is None:
        return None
    return eid, Quake(
        time=int(props.get("time") or 0),
        mag=float(props["mag"]),
        depth_km=float(geom[2]),
        lat=float(geom[1]),
        lon=float(geom[0]),
        felt=int(props.get("felt") or 0),
        alert=props.get("alert"),
        tsunami=int(props.get("tsunami") or 0),
        mmi=float(props.get("mmi") or 0.0),
    )


def _existing_ids(journal: ShadowJournal) -> tuple[set, set]:
    """Return (predicted_ids, resolved_ids) already in the journal."""
    predicted, resolved = set(), set()
    for rec in journal.records():
        pid = rec.payload.get("id")
        if rec.kind == "prediction":
            predicted.add(pid)
        elif rec.kind == "outcome":
            resolved.add(pid)
    return predicted, resolved


def tick(journal: ShadowJournal, feed_url: str) -> int:
    """Journal a prediction for every NEW event in the feed. Returns count added."""
    doc = _fetch_json(feed_url)
    if doc is None:
        return 0
    fit = _fitted_model()
    predicted, _ = _existing_ids(journal)
    added = 0
    for feat in doc.get("features", []):
        parsed = _quake_from_feature(feat)
        if parsed is None:
            continue
        eid, q = parsed
        if eid in predicted:
            continue
        prob = float(predict(fit, [q.features()])[0])
        issued = _iso_ms(q.time)
        journal.record_prediction(
            eid,
            hazard="earthquake",
            issued_at=issued,
            window_end=issued,  # impact assessment is instantaneous (no lead horizon)
            probability=prob,
            threshold=OPERATING_THRESHOLD,
            features=dict(zip(("magnitude", "depth_km", "abs_latitude", "ocean_proxy",
                              "gmpe_attenuation"), q.features())),
            model_version=_MODEL_VERSION,
        )
        added += 1
        print(f"[shadow] predicted {eid}: M{q.mag} depth {q.depth_km}km -> p={prob:.4f}")
    return added


def resolve(journal: ShadowJournal, *, now_ms: int) -> int:
    """Attach real outcomes for settled, still-unresolved predictions. Returns count."""
    predicted, resolved = _existing_ids(journal)
    pending = []
    for rec in journal.records():
        if rec.kind != "prediction":
            continue
        pid = rec.payload["id"]
        if pid in resolved:
            continue
        issued_ms = _ms_from_iso(rec.payload.get("issued_at", ""))
        if issued_ms and now_ms - issued_ms >= RESOLVE_GRACE_MS:
            pending.append(pid)
    n = 0
    for eid in pending:
        doc = _fetch_json(EVENT_DETAIL.format(eid=eid))
        if doc is None:
            continue
        parsed = _quake_from_feature(doc) if doc.get("id") else None
        if parsed is None:
            # USGS returns the event as a single Feature; wrap if needed.
            parsed = _quake_from_feature({"id": eid, **doc})
        if parsed is None:
            continue
        _, q = parsed
        occurred = bool(q.label_damaging())
        journal.attach_outcome(
            eid, occurred=occurred, observed_at=_iso_ms(now_ms),
            detail=f"alert={q.alert or 'none'} mmi={q.mmi}",
        )
        n += 1
        print(f"[shadow] resolved {eid}: damaging={occurred} (alert={q.alert} mmi={q.mmi})")
    return n


def _iso_ms(ms: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.UTC).isoformat()


def _ms_from_iso(s: str) -> int:
    import datetime as dt
    try:
        return int(dt.datetime.fromisoformat(s).timestamp() * 1000)
    except ValueError:
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="disastermind.live.usgs_shadow",
        description="Drive a real shadow season from the live USGS earthquake feed.",
    )
    ap.add_argument("--journal", default="shadow/usgs_season.jsonl")
    ap.add_argument("--feed-url", default=DEFAULT_FEED)
    ap.add_argument("--mode", choices=["tick", "resolve", "both"], default="both")
    ap.add_argument("--now-ms", type=int, default=None,
                    help="override 'now' in ms epoch (testing/determinism)")
    args = ap.parse_args(argv)

    import os
    os.makedirs(os.path.dirname(args.journal) or ".", exist_ok=True)
    journal = ShadowJournal(args.journal)

    added = resolved_n = 0
    if args.mode in ("tick", "both"):
        added = tick(journal, args.feed_url)
    if args.mode in ("resolve", "both"):
        now_ms = args.now_ms if args.now_ms is not None else _now_ms()
        resolved_n = resolve(journal, now_ms=now_ms)

    ok = journal.verify_chain()
    print(json.dumps({
        "predictions_added": added, "outcomes_resolved": resolved_n,
        "chain_intact": ok, "journal": args.journal,
    }))
    return 0 if ok else 1


def _now_ms() -> int:
    import datetime as dt
    return int(dt.datetime.now(tz=dt.UTC).timestamp() * 1000)


if __name__ == "__main__":
    raise SystemExit(main())
