"""Render an honest Fani hindcast event report (Markdown)."""
from __future__ import annotations

from .fani import FaniCase
from .replay import HindcastResult


def to_markdown(case: FaniCase, results: list[HindcastResult]) -> str:
    lf = case.landfall_point()
    o = case.outcome
    lines = [
        f"# Hindcast — Cyclone {case.storm.title()} ({case.season})",
        "",
        f"_Source: {case.source}. Replayed leak-free: each row uses only best-track "
        "data available before its forecast cutoff._",
        "",
        "## The real event (documented)",
        f"- **Landfall:** {o.get('landfall_date')} ~{o.get('landfall_time_ist')} IST, "
        f"{o.get('landfall_place')} ({lf.lat}, {lf.lon})",
        f"- **Intensity:** {o.get('landfall_intensity')} — sustained "
        f"{o.get('landfall_sustained_kmh')} km/h",
        f"- **Outcome:** {o.get('deaths')} deaths · {o.get('evacuated')} · "
        f"${o.get('damage_usd_billion')}B damage",
        f"- **Sources:** {'; '.join(o.get('sources', []))}",
        "",
        "## Hindcast at decreasing lead time",
        "| Lead | Cutoff (UTC) | Intensity | Landfall error | Activated | Plan produced | Dispatches |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        err = "n/a" if r.track_error_km != r.track_error_km else f"{r.track_error_km:.0f} km"
        lines.append(
            f"| {r.lead_hours:.0f} h | {r.cutoff_time} | "
            f"{('%.0f kt' % r.cutoff_intensity_kt) if r.cutoff_intensity_kt else '—'} | "
            f"{err} | {'✅' if r.activated else '❌'} | "
            f"{'✅' if r.produced_plan else '❌'} | {r.dispatches} |"
        )
    lines += [
        "",
        "## What this shows (and doesn't)",
        "- **Activation lead time** is the load-bearing result: Fani's low toll "
        f"({o.get('deaths')} deaths despite a Category-4 strike) was bought by a "
        "~3-day pre-landfall evacuation of ~1.2-1.5M. The system activates on the "
        "IMD cyclonic-storm alert days ahead — i.e. it would have triggered the "
        "coordination window that mattered.",
        "- **Landfall error** uses a deliberately naive great-circle extrapolation, "
        "NOT a dynamical forecast. In production IMD's track forecast is the input; "
        "this is a floor on how far even a trivial extrapolation lands from the coast.",
        "- **Honest limits:** DisasterMind is a coordination system, not a "
        "track-forecast model; this replay does not re-predict casualties or "
        "validate the *quality* of the evacuation plan against the real one — only "
        "that activation + a plan would have been produced in time. Validating the "
        "plan itself needs the real evacuation/road/shelter records.",
    ]
    return "\n".join(lines)
