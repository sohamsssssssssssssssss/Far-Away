"""External outcome cross-check — does model risk match REAL declared disasters?

The in-house validation scores the flood model against GloFAS discharge exceedance
(a hydrological proxy). This module adds an INDEPENDENT survey-grade check: GDACS
(UN OCHA / EC-JRC) declared real Indian flood events — authoritative outcomes that
**never entered training or the discharge labels** — and asks whether the model's
risk actually rises on the days a real flood was declared.

Two honest questions:

  * **Separation.** Treating "this date falls in a GDACS-declared flood window"
    as the label, what AUC does the model's risk achieve? If the model is real,
    declared-disaster days score higher than quiet days — measured on a source
    fully external to the model.
  * **Severity gradient.** Do GDACS *Red* (most severe) event days draw higher
    model risk than *Orange* days? A genuine model should track declared severity.

Honesty: GDACS flood events are largely country-level (no precise sub-basin
location), so this is a TEMPORAL national cross-check, not a per-cell one — it
confirms the model elevates during real declared events, not that it pinpointed
the basin. Alert level is GDACS's severity classification, not a casualty count.
Stdlib only; the committed GDACS fixture is the only external input.
"""
from __future__ import annotations

import datetime as _dt
import json
import os

from ..eval.metrics import roc_auc

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "gdacs_india_disasters_2010_2023.json"
)
#: GDACS alert levels in ascending severity (case-normalised on load).
ALERT_RANK = {"green": 0, "orange": 1, "red": 2}


def load_gdacs(path: str = FIXTURE) -> list[dict]:
    """Load the committed GDACS declared-disaster catalog (no network)."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["events"]


def _date(s: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def flood_event_days(
    events: list[dict], *, min_alert: str = "orange"
) -> dict[_dt.date, int]:
    """``{date: max alert rank}`` for every day inside a declared FLOOD window.

    Only GDACS flood (``FL``) events at or above ``min_alert`` are counted — the
    severe declared events a real model should clearly flag.
    """
    floor = ALERT_RANK[min_alert.lower()]
    days: dict[_dt.date, int] = {}
    for e in events:
        if e.get("eventtype") != "FL":
            continue
        rank = ALERT_RANK.get(str(e.get("alertlevel", "")).lower(), -1)
        if rank < floor:
            continue
        d0, d1 = _date(e.get("fromdate", "")), _date(e.get("todate", ""))
        if d0 is None:
            continue
        d1 = d1 or d0
        day = d0
        while day <= d1:
            days[day] = max(days.get(day, 0), rank)
            day += _dt.timedelta(days=1)
    return days


def cross_check_flood(
    dates: list[_dt.date],
    risks: list[float],
    events: list[dict],
    *,
    min_alert: str = "orange",
) -> dict:
    """AUC of model risk separating GDACS-declared flood days from quiet days.

    ``dates``/``risks`` are the per-row date and model risk (e.g. the flood test
    set: one max-over-sites risk per date, or per (site,date) row). The label is
    purely external (GDACS). Also reports the severity gradient: mean model risk
    on Red vs Orange vs quiet days.
    """
    if len(dates) != len(risks):
        raise ValueError("dates / risks length mismatch")
    event_days = flood_event_days(events, min_alert=min_alert)
    y = [1 if d in event_days else 0 for d in dates]
    n_pos = sum(y)
    result: dict = {
        "source": "GDACS (UN/EC) declared flood events — external to training",
        "min_alert": min_alert,
        "n_rows": len(dates),
        "n_declared_event_rows": n_pos,
        "auc_vs_declared_events": round(roc_auc(y, risks), 4) if 0 < n_pos < len(y) else None,
    }
    # severity gradient: mean risk by GDACS class of the day
    buckets: dict[str, list[float]] = {"red": [], "orange": [], "quiet": []}
    for d, r in zip(dates, risks):
        rank = event_days.get(d)
        key = "red" if rank == 2 else "orange" if rank == 1 else "quiet"
        buckets[key].append(r)
    result["mean_risk_by_class"] = {
        k: (round(sum(v) / len(v), 4) if v else None) for k, v in buckets.items()
    }
    result["n_by_class"] = {k: len(v) for k, v in buckets.items()}
    return result


def to_markdown(result: dict) -> str:
    g = result["mean_risk_by_class"]
    lines = [
        "## External cross-check vs GDACS declared floods (survey-grade)",
        f"_{result['source']}_",
        "",
        f"- Declared-event days in window: **{result['n_declared_event_rows']}** / "
        f"{result['n_rows']} rows (alert >= {result['min_alert']})",
    ]
    if result["auc_vs_declared_events"] is not None:
        lines.append(
            f"- **AUC separating declared-flood days from quiet days: "
            f"{result['auc_vs_declared_events']}** (label is fully external to the model)"
        )
    lines += [
        "- Severity gradient (mean model risk): "
        f"Red {g.get('red')} · Orange {g.get('orange')} · quiet {g.get('quiet')}",
        "",
        "_Honest scope: GDACS flood events are country-level, so this is a temporal "
        "national cross-check (the model elevates during real declared events), not "
        "a per-basin localisation; alert level is a severity class, not a toll._",
    ]
    return "\n".join(lines)
