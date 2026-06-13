"""``python -m disastermind.hindcast.walkthrough`` — the narrated hero demo.

A guided, decision-maker-facing walkthrough of a real cyclone (Fani 2019 or
Amphan 2020), told as a timeline of command decisions rather than a metrics dump.
At each forecast cutoff it answers the three questions a commander actually asks:

    1. What do we KNOW right now? (intensity, track, lead time)
    2. What does the system RECOMMEND? (activate, plan, dispatch — and what is
       still a human's call)
    3. What is the COST OF WAITING? (the clearance deadline, who gets left behind)

It reuses the leak-free replay engine (:mod:`disastermind.hindcast.replay`) — each
step sees only best-track data available before its cutoff — so nothing here is
hindsight. The closing panel scores the run against the documented real outcome.

Offline, stdlib-only, deterministic. Run::

    python -m disastermind.hindcast.walkthrough              # Fani 2019
    python -m disastermind.hindcast.walkthrough --storm amphan
    python -m disastermind.hindcast.walkthrough --plain      # no ANSI colour
"""
from __future__ import annotations

import argparse
from dataclasses import asdict

from .fani import load_amphan, load_fani
from .replay import run_hindcast

LEADS = (72.0, 48.0, 36.0, 24.0, 12.0)
LOADERS = {"fani": load_fani, "amphan": load_amphan}

# The authority threshold the commander matrix enforces: a mass evacuation
# (> 10,000 people) is never autonomous — it is recommended, a human orders it.
MASS_EVAC_THRESHOLD = 10_000


class _Style:
    """Minimal ANSI styling, switchable off for logs/CI."""

    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _w(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def bold(self, s: str) -> str:
        return self._w("1", s)

    def dim(self, s: str) -> str:
        return self._w("2", s)

    def red(self, s: str) -> str:
        return self._w("1;31", s)

    def amber(self, s: str) -> str:
        return self._w("1;33", s)

    def green(self, s: str) -> str:
        return self._w("1;32", s)

    def cyan(self, s: str) -> str:
        return self._w("1;36", s)


def _rule(st: _Style, ch: str = "─") -> str:
    return st.dim(ch * 74)


def _header(st: _Style, case) -> list[str]:
    o = case.outcome
    lines = [
        "",
        st.bold(st.cyan(f"  DISASTERMIND — COMMAND WALKTHROUGH: Cyclone {case.storm.title()}")),
        _rule(st),
        st.dim("  A leak-free replay: at every step the system sees only the data a"),
        st.dim("  real commander had at that moment. Nothing below is hindsight."),
        "",
        st.bold("  THE REAL EVENT (documented, for scoring at the end):"),
        f"    Landfall   {o['landfall_place']} · {o['landfall_date']} {o.get('landfall_time_ist','')}",
        f"    Intensity  {o['landfall_intensity']} ({o['landfall_sustained_kmh']} km/h sustained)",
        f"    Outcome    {o['deaths']} deaths · {o['evacuated']} evacuated · "
        f"${o['damage_usd_billion']}B damage",
        st.dim(f"    Sources    {o['sources']}"),
        "",
    ]
    return lines


def _step(st: _Style, r: dict, case) -> list[str]:
    lead = int(r["lead_hours"])
    mass_evac = (case.outcome.get("evacuated_count") or 1_500_000) > MASS_EVAC_THRESHOLD
    knob = st.green if r["activated"] else st.dim
    lines = [
        _rule(st),
        st.bold(f"  T − {lead:>2} h   ") + st.dim(f"(cutoff {r['cutoff_time']} UTC)"),
        "",
        st.bold("  1. WHAT WE KNOW"),
        f"     Storm intensity at cutoff : {r['cutoff_intensity_kt']} kt",
        f"     Projected landfall        : {r['track_error_km']:.0f} km from where it hit",
        st.dim("       (naive great-circle extrapolation — a floor, not IMD's forecast)"),
        "",
        st.bold("  2. WHAT THE SYSTEM RECOMMENDS"),
        f"     Activate coordination     : {knob('YES' if r['activated'] else 'no')}",
        st.dim(f"       basis: {r['activation_basis']}"),
        f"     Response plan produced    : {'YES' if r['produced_plan'] else 'no'} "
        f"({r['dispatches']} field dispatches, {r['routes']} evacuation routes)",
    ]
    if r["activated"] and mass_evac:
        lines += [
            "     Mass-evacuation order     : "
            + st.amber("RECOMMENDED → awaits human commander"),
            st.dim("       > 10,000 people crosses the authority threshold — the system"),
            st.dim("       never issues this autonomously; it recommends, a human orders."),
        ]
    lines += [
        "",
        st.bold("  3. THE COST OF WAITING"),
    ]
    if lead >= 48:
        lines.append(
            st.green("     Full lead still available — ordering now clears the zone in time.")
        )
    elif lead >= 24:
        lines.append(
            st.amber("     Window tightening — every hour now is a cohort not yet moved;")
        )
        lines.append(
            st.amber("     transport-dependent and hospitalised groups must move FIRST.")
        )
    else:
        lines.append(
            st.red("     Past the clearance deadline for the slowest cohorts — anyone")
        )
        lines.append(
            st.red("     not already moving may not clear; vertical (in-place) shelter")
        )
        lines.append(st.red("     becomes the fallback for them."))
    lines.append("")
    return lines


def _closing(st: _Style, results: list[dict], case) -> list[str]:
    o = case.outcome
    first = next((r for r in results if r["activated"]), None)
    earliest_lead = int(first["lead_hours"]) if first else 0
    lines = [
        _rule(st, "═"),
        st.bold(st.cyan("  SCORED AGAINST REALITY")),
        "",
        f"  • Earliest activation        : {st.green(f'T − {earliest_lead} h')} before landfall",
        f"  • Real evacuation that ran   : {o['evacuated']}",
        f"  • Real death toll            : {o['deaths']} "
        + st.dim("(low for a Category-4 strike — bought by the days-ahead evacuation)"),
        "",
        st.bold("  THE HONEST BOUNDARY (what this does and does not show):"),
        st.dim("    ✓ The system would have ACTIVATED and produced a plan in the window"),
        st.dim("      that mattered — days before landfall."),
        st.dim("    ✗ It does NOT re-forecast the track (IMD's forecast is the real input)"),
        st.dim("      and does NOT validate the plan's quality against the actual"),
        st.dim("      evacuation — that needs agency road/shelter/response records."),
        st.dim("    → See docs/TECHNICAL_REPORT.md §6 and the in-console Limitations tab."),
        _rule(st, "═"),
        "",
    ]
    return lines


def render(storm: str, *, color: bool) -> str:
    st = _Style(color)
    case = LOADERS[storm]()
    results = [asdict(run_hindcast(case, lead_hours=h)) for h in LEADS]
    out: list[str] = []
    out += _header(st, case)
    for r in results:
        out += _step(st, r, case)
    out += _closing(st, results, case)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="disastermind.hindcast.walkthrough",
        description="Narrated command walkthrough of a real cyclone (leak-free replay).",
    )
    ap.add_argument("--storm", choices=sorted(LOADERS), default="fani")
    ap.add_argument("--plain", action="store_true", help="disable ANSI colour")
    args = ap.parse_args(argv)
    print(render(args.storm, color=not args.plain))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
