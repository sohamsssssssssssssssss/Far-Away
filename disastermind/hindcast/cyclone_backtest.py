"""Per-region backtest across ALL real landfalling North-Indian-Ocean cyclones.

Extends the single-storm hindcast (Fani, Amphan) to the whole committed IBTrACS
landfalling set (92 storms, 1990-present), to answer one pitch question with real
data: *"does the activation pattern hold nationally, not just on two cherry-picked
storms?"* For every storm it locates landfall, classifies the coastal region, and
checks whether the system would have activated at a standard forecast cutoff
(an IMD cyclonic-storm alert — sustained wind >= 34 kt — present in the track
before the cutoff). Results aggregate per region.

Source: NOAA IBTrACS v04r01 North Indian Ocean, landfalling subset (committed
fixture `fixtures/ibtracs_ni_landfalling.json`). No network.

HONESTY (stated, not hidden):
  * Region classification is **approximate bounding boxes**, NOT official state
    polygons — a landfall is tagged to the nearest coastal-region box, and
    landfalls outside India (Bangladesh / Myanmar / Sri Lanka / Pakistan / Oman)
    are labelled as such rather than forced onto an Indian state.
  * "Activation" tests the IMD-alert trigger the real system uses; it does NOT
    re-forecast the track (IMD's dynamical forecast is the production input) — so
    this measures *coordination-window* coverage, the same honest claim as the
    Fani/Amphan replays.
  * A storm with no usable pre-cutoff wind record is reported as `unknown`, not
    silently counted as activated.

Pure, deterministic, stdlib-only.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ibtracs_ni_landfalling.json")
_FMT = "%Y-%m-%d %H:%M:%S"

#: IMD cyclonic-storm alert threshold (sustained wind, knots) — the activation trigger.
CYCLONE_ALERT_KT = 34.0
#: Standard forecast cutoff for the national backtest (hours before landfall).
DEFAULT_LEAD_HOURS = 72

# Approximate coastal-region bounding boxes (lat_min, lat_max, lon_min, lon_max).
# Deliberately coarse + explicitly labelled approximate (see module docstring).
_REGIONS: tuple[tuple[str, float, float, float, float], ...] = (
    ("West Bengal / Sundarbans", 21.0, 22.6, 87.4, 89.6),
    ("Odisha", 18.9, 21.0, 84.4, 87.4),
    ("Andhra Pradesh", 13.4, 18.9, 79.8, 85.2),
    ("Tamil Nadu / Puducherry", 8.0, 13.4, 77.8, 80.6),
    ("Gujarat", 20.0, 24.5, 68.0, 73.2),
    ("Maharashtra / Konkan", 15.4, 20.0, 72.0, 74.2),
    # Non-India NI-basin landfalls — classified honestly, not forced onto India.
    ("Bangladesh", 20.6, 23.0, 89.0, 92.8),
    ("Myanmar", 12.0, 21.5, 92.0, 99.0),
    ("Sri Lanka", 5.8, 9.9, 79.5, 82.0),
    ("Pakistan / Makran", 23.0, 26.0, 60.0, 68.0),
    ("Oman / Arabia", 16.0, 26.0, 52.0, 60.0),
)


def _parse(t: str) -> datetime:
    return datetime.strptime(t, _FMT)


def classify_region(lat: float, lon: float) -> str:
    """Approximate coastal-region label for a landfall point (see docstring)."""
    for name, la0, la1, lo0, lo1 in _REGIONS:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return "Other / open-coast"


@dataclass
class StormResult:
    sid: str
    name: str
    season: int
    landfall_time: str | None
    landfall_lat: float | None
    landfall_lon: float | None
    region: str
    max_wind_kt: float | None
    cutoff_wind_kt: float | None
    activated: bool | None  # None = unknown (no usable pre-cutoff wind)


@dataclass
class RegionSummary:
    region: str
    storms: int
    activated: int
    unknown: int
    activation_rate: float  # over storms with a known verdict


@dataclass
class NationalBacktest:
    lead_hours: int
    total_storms: int
    india_landfalls: int
    activated: int
    unknown: int
    activation_rate: float
    regions: list[RegionSummary]
    storms: list[StormResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["regions"] = [r.__dict__ for r in self.regions]
        d["storms"] = [s.__dict__ for s in self.storms]
        return d


_INDIA_REGIONS = {
    "West Bengal / Sundarbans", "Odisha", "Andhra Pradesh",
    "Tamil Nadu / Puducherry", "Gujarat", "Maharashtra / Konkan",
}


def load_storms(path: str = FIXTURE) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return d if isinstance(d, list) else d.get("storms", d.get("events", []))


def _first_landfall(track: list[dict]) -> dict | None:
    """First track point at the coast (dist2land_km == 0)."""
    for p in track:
        d = p.get("dist2land_km")
        if d is not None and d <= 0.0:
            return p
    return None


def _wind_before(track: list[dict], cutoff: datetime) -> float | None:
    """Max sustained wind (kt) recorded at or before the cutoff, if any."""
    winds = [
        p["wind_kt"] for p in track
        if p.get("wind_kt") is not None and _parse(p["time"]) <= cutoff
    ]
    return max(winds) if winds else None


def backtest_storm(storm: dict, lead_hours: int = DEFAULT_LEAD_HOURS) -> StormResult:
    track = storm.get("track", [])
    lf = _first_landfall(track)
    region = "Other / open-coast"
    lf_time = lf_lat = lf_lon = None
    cutoff_wind = None
    activated: bool | None = None
    if lf is not None:
        lf_time, lf_lat, lf_lon = lf["time"], lf.get("lat"), lf.get("lon")
        if lf_lat is not None and lf_lon is not None:
            region = classify_region(lf_lat, lf_lon)
        cutoff = _parse(lf_time) - timedelta(hours=lead_hours)
        cutoff_wind = _wind_before(track, cutoff)
        if cutoff_wind is not None:
            activated = cutoff_wind >= CYCLONE_ALERT_KT
    return StormResult(
        sid=storm.get("sid", ""), name=storm.get("name", "UNNAMED"),
        season=int(storm.get("season", 0)), landfall_time=lf_time,
        landfall_lat=lf_lat, landfall_lon=lf_lon, region=region,
        max_wind_kt=storm.get("max_wind_kt"), cutoff_wind_kt=cutoff_wind,
        activated=activated,
    )


def run_national_backtest(
    lead_hours: int = DEFAULT_LEAD_HOURS, path: str = FIXTURE
) -> NationalBacktest:
    """Backtest every committed landfalling storm; aggregate per region."""
    results = [backtest_storm(s, lead_hours) for s in load_storms(path)]
    by_region: dict[str, list[StormResult]] = {}
    for r in results:
        by_region.setdefault(r.region, []).append(r)

    regions: list[RegionSummary] = []
    for name, rs in sorted(by_region.items(), key=lambda kv: -len(kv[1])):
        known = [r for r in rs if r.activated is not None]
        act = sum(1 for r in known if r.activated)
        regions.append(RegionSummary(
            region=name, storms=len(rs), activated=act,
            unknown=sum(1 for r in rs if r.activated is None),
            activation_rate=round(act / len(known), 3) if known else 0.0,
        ))

    known_all = [r for r in results if r.activated is not None]
    act_all = sum(1 for r in known_all if r.activated)
    india = sum(1 for r in results if r.region in _INDIA_REGIONS)
    notes = [
        f"{len(results)} real landfalling NI-basin cyclones (IBTrACS v04r01); "
        f"{india} classified to an Indian coastal region, the rest to neighbouring "
        "coasts (Bangladesh/Myanmar/Sri Lanka/…) — classified honestly, not forced.",
        "Region = approximate bounding box, NOT official state polygons.",
        "Activation = IMD cyclonic-storm alert (>=34 kt) present before the "
        f"{lead_hours} h cutoff; storms with no usable pre-cutoff wind are 'unknown', "
        "never counted as activated.",
        "This measures coordination-window coverage, not track-forecast skill "
        "(IMD's dynamical forecast is the production input).",
    ]
    return NationalBacktest(
        lead_hours=lead_hours, total_storms=len(results), india_landfalls=india,
        activated=act_all, unknown=sum(1 for r in results if r.activated is None),
        activation_rate=round(act_all / len(known_all), 3) if known_all else 0.0,
        regions=regions, storms=results, notes=notes,
    )


def to_markdown(bt: NationalBacktest) -> str:
    lines = [
        "# National Cyclone Backtest — all real landfalling NI-basin storms",
        "",
        f"_Source: NOAA IBTrACS v04r01 landfalling subset · {bt.total_storms} storms · "
        f"{bt.lead_hours} h forecast cutoff._",
        "",
        f"- **{bt.total_storms}** real storms · **{bt.india_landfalls}** Indian-coast "
        f"landfalls · activation rate **{bt.activation_rate:.0%}** "
        f"({bt.activated} activated, {bt.unknown} unknown wind record)",
        "",
        "## By coastal region",
        "| Region | Storms | Activated | Activation rate | Unknown |",
        "|---|---|---|---|---|",
    ]
    for r in bt.regions:
        lines.append(
            f"| {r.region} | {r.storms} | {r.activated} | {r.activation_rate:.0%} | {r.unknown} |"
        )
    lines += ["", "## Honest limits", *[f"- {n}" for n in bt.notes]]
    return "\n".join(lines)
