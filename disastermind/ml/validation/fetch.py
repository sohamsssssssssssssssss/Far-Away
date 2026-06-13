"""Fixture builders — fetch REAL hazard data and commit it for offline validation.

This is the only network-using module in :mod:`disastermind.ml.validation`. It
(re)builds the committed fixtures that the flood / fire / earthquake validation
datasets load offline, so the validation suite itself stays deterministic and
network-free while the data underneath it is real:

  * **flood** — GloFAS-ERA5 river-discharge reanalysis via the free Open-Meteo
    flood API plus ERA5 daily precipitation via the Open-Meteo historical archive,
    for 12 Indian river-basin sites (Brahmaputra, Ganga, Kosi, Yamuna, Godavari,
    Krishna, Mahanadi, Narmada, Barak), 2010-2023 daily.
  * **fire** — real wildfire occurrences from the USDA Forest Service FPA-FOD
    ("Karen Short") fire-occurrence database, served publicly by NIFC's ArcGIS
    feature service, joined with ERA5 daily fire weather (temperature, humidity,
    wind, precipitation) for 12 fire-prone Pacific-Northwest cells, 2012-2018
    daily. The public layer's 2012-2018 coverage is OR+WA (verified server-side),
    so the study region is the PNW — documented, not hidden. NASA FIRMS country
    archives are the intended primary source for Indian fire detections; the
    FIRMS host is unreachable from restricted networks, so :func:`fetch_fire`
    documents the swap and the fixture records the provenance actually used.
    FPA-FOD is agency-reported ground truth (better than satellite hotspots:
    no cloud gaps, no false detections from gas flares).
  * **quake** — the USGS FDSN event catalog (M4.5+), already committed as
    ``usgs_catalog_2013_2017.json``; ``fetch_quake`` can refresh/extend it.

Run manually (network required), e.g.::

    python -m disastermind.ml.validation.fetch flood
    python -m disastermind.ml.validation.fetch fire
    python -m disastermind.ml.validation.fetch quake

Everything else in the package treats the produced JSON as read-only input.
Stdlib only: ``urllib`` + ``json``; no API keys, both sources are free and public.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

FLOOD_FIXTURE = os.path.join(FIXTURES, "openmeteo_glofas_india_2010_2023.json")
FIRE_FIXTURE = os.path.join(FIXTURES, "fpafod_era5_fire_2012_2018.json")
FIRE_INDIA_FIXTURE = os.path.join(FIXTURES, "firms_era5_fire_india_2015_2024.json")
QUAKE_FIXTURE = os.path.join(FIXTURES, "usgs_catalog_2013_2017.json")
#: Survey-grade external outcome catalog (GDACS UN/EC declared disaster events).
GDACS_FIXTURE = os.path.join(FIXTURES, "gdacs_india_disasters_2010_2023.json")

#: Flood study window (GloFAS-ERA5 reanalysis covers 1984+; ERA5 covers 1940+).
FLOOD_START, FLOOD_END = "2010-01-01", "2023-12-31"
#: Fire study window (FPA-FOD ends 2018; ERA5 fully covers it).
FIRE_START, FIRE_END = "2012-01-01", "2018-12-31"
#: External-outcome window (GDACS declared events to cross-check the models).
GDACS_START, GDACS_END = "2010-01-01", "2023-12-31"

_FLOOD_API = "https://flood-api.open-meteo.com/v1/flood"
_ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
_FPAFOD_API = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/"
    "Historic_Fires_Karen_Short_1992_to_2018/FeatureServer/0/query"
)
_GDACS_API = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"


@dataclass(frozen=True)
class FloodSite:
    """One river-basin validation site (a real Indian flood-prone location).

    ``setting`` is the equity axis for the fairness audit: whether the site is an
    urban centre or a rural/floodplain community. ``region`` groups sites into
    blocks for leave-one-region-out cross-validation.
    """

    name: str
    basin: str
    region: str
    setting: str  # "urban" | "rural"
    lat: float
    lon: float


#: 12 real Indian flood-prone sites across 9 basins / 5 macro-regions, tagged
#: urban/rural so the fairness audit can test for systematic under-protection
#: of rural floodplain communities (the PRD's equity premise).
FLOOD_SITES: tuple[FloodSite, ...] = (
    FloodSite("guwahati", "brahmaputra", "northeast", "urban", 26.18, 91.74),
    FloodSite("majuli", "brahmaputra", "northeast", "rural", 26.95, 94.17),
    FloodSite("silchar", "barak", "northeast", "urban", 24.82, 92.80),
    FloodSite("patna", "ganga", "east", "urban", 25.62, 85.17),
    FloodSite("supaul", "kosi", "east", "rural", 26.12, 86.60),
    FloodSite("cuttack", "mahanadi", "east", "urban", 20.47, 85.88),
    FloodSite("kendrapara", "mahanadi", "east", "rural", 20.50, 86.42),
    FloodSite("delhi", "yamuna", "north", "urban", 28.66, 77.23),
    FloodSite("rajahmundry", "godavari", "south", "urban", 17.00, 81.78),
    FloodSite("bhadrachalam", "godavari", "south", "rural", 17.67, 80.88),
    FloodSite("vijayawada", "krishna", "south", "urban", 16.52, 80.62),
    FloodSite("bharuch", "narmada", "west", "urban", 21.71, 72.99),
)


@dataclass(frozen=True)
class FireCell:
    """One fire-validation grid cell (centre of a 1 deg box, a real fire regime).

    ``region`` blocks cells for leave-one-region-out CV; ``half_deg`` is the half
    width of the label box around the centre. The box is deliberately wide (~100
    km) because the public FPA-FOD layer is a LARGE-fires subset — the label is
    "a consequential wildfire ignited near this cell today".
    """

    name: str
    state: str
    region: str
    lat: float
    lon: float
    half_deg: float = 0.5


#: 12 real fire-prone cells across the Pacific Northwest, the region the public
#: FPA-FOD layer fully covers for 2012-2018 (server-side audit: OR 15.8k + WA
#: 10.4k fires vs <100 elsewhere — coverage is honest-by-construction, not
#: assumed). The cells span 5 genuinely different fire regimes (wet west-Cascade
#: forest vs dry east-side shrub-steppe, OR vs WA) so leave-one-region-out CV
#: tests regime transfer, not neighbouring-pixel memorisation.
FIRE_CELLS: tuple[FireCell, ...] = (
    FireCell("rogue-umpqua", "OR", "or-west", 42.90, -123.30),
    FireCell("willamette-cascades", "OR", "or-west", 44.20, -122.30),
    FireCell("mt-hood", "OR", "or-west", 45.20, -121.90),
    FireCell("klamath-basin", "OR", "or-east", 42.40, -121.70),
    FireCell("deschutes", "OR", "or-east", 43.90, -121.20),
    FireCell("john-day", "OR", "or-east", 44.60, -119.20),
    FireCell("blue-mountains", "OR", "or-east", 45.30, -118.40),
    FireCell("gifford-pinchot", "WA", "wa-west", 46.10, -121.90),
    FireCell("wenatchee", "WA", "wa-east", 47.60, -120.60),
    FireCell("yakima", "WA", "wa-east", 46.60, -120.80),
    FireCell("methow-okanogan", "WA", "wa-east", 48.40, -119.90),
    FireCell("colville", "WA", "wa-east", 48.50, -117.90),
)


# --------------------------------------------------------------------------- http
def _ssl_context() -> ssl.SSLContext:
    """Default SSL context, falling back to the system CA bundle.

    python.org macOS builds ship without a trust store wired into OpenSSL; when
    the default context cannot verify (no local issuer certs), fall back to the
    OS bundle at ``/etc/ssl/cert.pem`` so the fetch still verifies TLS properly.
    """
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats().get("x509_ca") and os.path.exists("/etc/ssl/cert.pem"):
        ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx


def _get_json(url: str, params: dict | None = None, *, retries: int = 4) -> dict:
    """GET ``url`` (+ query params) and parse JSON, with linear-backoff retries."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120, context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}") from last


def _round_list(values: list, ndigits: int) -> list:
    """Round a numeric series for fixture compactness; None (gap) passes through."""
    return [None if v is None else round(float(v), ndigits) for v in values]


# --------------------------------------------------------------------------- flood
def fetch_flood(out_path: str = FLOOD_FIXTURE) -> dict:
    """Build the flood fixture: daily GloFAS discharge + ERA5 precipitation.

    One record per :data:`FLOOD_SITES` entry with aligned daily arrays
    (``discharge`` m3/s, ``precip`` mm) for :data:`FLOOD_START`..:data:`FLOOD_END`.
    Both series are *reanalysis of what actually happened* — the discharge series
    is the real outcome the flood model is validated against.
    """
    sites = []
    for site in FLOOD_SITES:
        flood = _get_json(
            _FLOOD_API,
            {
                "latitude": site.lat,
                "longitude": site.lon,
                "daily": "river_discharge",
                "start_date": FLOOD_START,
                "end_date": FLOOD_END,
            },
        )
        weather = _get_json(
            _ARCHIVE_API,
            {
                "latitude": site.lat,
                "longitude": site.lon,
                "daily": "precipitation_sum",
                "start_date": FLOOD_START,
                "end_date": FLOOD_END,
                "timezone": "UTC",
            },
        )
        days = flood["daily"]["time"]
        discharge = _round_list(flood["daily"]["river_discharge"], 2)
        precip = _round_list(weather["daily"]["precipitation_sum"], 1)
        if not (len(days) == len(discharge) == len(precip)):
            raise RuntimeError(f"series misaligned for {site.name}")
        sites.append({**asdict(site), "start": days[0], "discharge": discharge, "precip": precip})
        print(f"flood: {site.name}: {len(days)} days", file=sys.stderr)

    fixture = {
        "source": {
            "discharge": "GloFAS-ERA5 v4 river-discharge reanalysis (Open-Meteo flood API)",
            "precip": "ERA5 daily precipitation (Open-Meteo historical archive)",
            "window": [FLOOD_START, FLOOD_END],
            "license": "open data, free APIs, no key",
        },
        "sites": sites,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, separators=(",", ":"))
    return fixture


# --------------------------------------------------------------------------- fire
def _parse_fpafod_date(raw: str) -> str | None:
    """``m/d/yyyy`` -> ISO ``yyyy-mm-dd`` (the service stores dates as strings)."""
    try:
        month, day, year = (int(part) for part in str(raw).split("/"))
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, AttributeError):
        return None


def _fetch_fpafod_window() -> list[dict]:
    """Bulk-download every FPA-FOD record in the fire window (one pass, paged).

    The hosted layer's WHERE clause is unreliable on double-typed fields
    (LATITUDE/LONGITUDE comparisons return all-or-nothing), but integer fields
    filter correctly — so we filter server-side on FIRE_YEAR only, page in
    OBJECTID order for stability, and box-filter per cell client-side.
    """
    where = f"FIRE_YEAR >= {int(FIRE_START[:4])} AND FIRE_YEAR <= {int(FIRE_END[:4])}"
    rows: list[dict] = []
    offset = 0
    while True:
        page = _get_json(
            _FPAFOD_API,
            {
                "where": where,
                "outFields": "DISCOVERY_DATE,FIRE_SIZE,LATITUDE,LONGITUDE",
                "returnGeometry": "false",
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": 2000,
                "f": "json",
            },
        )
        feats = page.get("features", [])
        for f in feats:
            attrs = f.get("attributes", {})
            date = _parse_fpafod_date(attrs.get("DISCOVERY_DATE"))
            if date is None or attrs.get("LATITUDE") is None or attrs.get("LONGITUDE") is None:
                continue
            rows.append(
                {
                    "date": date,
                    "lat": float(attrs["LATITUDE"]),
                    "lon": float(attrs["LONGITUDE"]),
                    "size_acres": float(attrs.get("FIRE_SIZE") or 0.0),
                }
            )
        print(f"fire: FPA-FOD download: {len(rows)} rows", file=sys.stderr)
        # ArcGIS may return short pages while more data remains (transfer
        # limits), so only an EMPTY page terminates; advance by what arrived.
        if not feats:
            return rows
        offset += len(feats)


def _fires_in_cell(all_fires: list[dict], cell: FireCell) -> list[dict]:
    """Real fire records inside ``cell``'s box, as ``{"date", "size_acres"}``."""
    return [
        {"date": f["date"], "size_acres": f["size_acres"]}
        for f in all_fires
        if abs(f["lat"] - cell.lat) <= cell.half_deg
        and abs(f["lon"] - cell.lon) <= cell.half_deg
    ]


def fetch_fire(out_path: str = FIRE_FIXTURE) -> dict:
    """Build the fire fixture: ERA5 daily fire weather + real fire occurrences.

    One record per :data:`FIRE_CELLS` entry: aligned daily weather arrays and the
    real fire events (epoch-ms discovery date + acres) discovered inside the cell.
    Labels are *agency-reported wildfire occurrences* (FPA-FOD), i.e. genuine
    outcomes, not a synthetic or proxy signal.
    """
    all_fires = _fetch_fpafod_window()
    cells = []
    for cell in FIRE_CELLS:
        weather = _get_json(
            _ARCHIVE_API,
            {
                "latitude": cell.lat,
                "longitude": cell.lon,
                "daily": (
                    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                    "wind_speed_10m_max,relative_humidity_2m_min"
                ),
                "start_date": FIRE_START,
                "end_date": FIRE_END,
                "timezone": "UTC",
            },
        )
        daily = weather["daily"]
        fires = _fires_in_cell(all_fires, cell)
        cells.append(
            {
                **asdict(cell),
                "start": daily["time"][0],
                "tmax": _round_list(daily["temperature_2m_max"], 1),
                "tmin": _round_list(daily["temperature_2m_min"], 1),
                "precip": _round_list(daily["precipitation_sum"], 1),
                "wind_max": _round_list(daily["wind_speed_10m_max"], 1),
                "rh_min": _round_list(daily["relative_humidity_2m_min"], 0),
                "fires": fires,
            }
        )
        print(f"fire: {cell.name}: {len(fires)} real fires", file=sys.stderr)

    fixture = {
        "source": {
            "fires": (
                "USDA Forest Service FPA-FOD national fire-occurrence database "
                "(Short, K.C.), public NIFC ArcGIS service. NASA FIRMS is the "
                "intended primary for India; host unreachable from this network, "
                "FPA-FOD substituted (agency ground truth, 1992-2018)."
            ),
            "weather": "ERA5 daily fire weather (Open-Meteo historical archive)",
            "window": [FIRE_START, FIRE_END],
            "license": "open data, free APIs, no key",
        },
        "cells": cells,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, separators=(",", ":"))
    return fixture


# --------------------------------------------------------------------------- quake
def fetch_quake(out_path: str = QUAKE_FIXTURE, start: str = "2013-01-01", end: str = "2018-01-01") -> list:
    """Refresh the USGS M4.5+ catalog fixture (monthly pages to respect limits)."""
    base = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    rows: list[dict] = []
    # Page by calendar month: the FDSN endpoint caps each response at 20k events.
    year, month = int(start[:4]), int(start[5:7])
    end_year, end_month = int(end[:4]), int(end[5:7])
    while (year, month) < (end_year, end_month):
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        data = _get_json(
            base,
            {
                "format": "geojson",
                "starttime": f"{year:04d}-{month:02d}-01",
                "endtime": f"{ny:04d}-{nm:02d}-01",
                "minmagnitude": 4.5,
                "orderby": "time",
            },
        )
        for feat in data.get("features", []):
            p = feat.get("properties", {})
            g = (feat.get("geometry") or {}).get("coordinates") or [None, None, None]
            rows.append(
                {
                    "time": p.get("time"),
                    "mag": p.get("mag"),
                    "depth_km": g[2],
                    "lat": g[1],
                    "lon": g[0],
                    "sig": p.get("sig"),
                    "felt": p.get("felt") or 0,
                    "cdi": p.get("cdi") or 0,
                    "mmi": p.get("mmi") or 0,
                    "alert": p.get("alert"),
                    "tsunami": p.get("tsunami") or 0,
                }
            )
        print(f"quake: {year}-{month:02d}: {len(rows)} cumulative", file=sys.stderr)
        year, month = ny, nm
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, separators=(",", ":"))
    return rows


# ------------------------------------------------------------ survey-grade outcomes
def fetch_gdacs(out_path: str = GDACS_FIXTURE) -> dict:
    """Build the survey-grade external-outcome catalog from GDACS (UN/EC).

    GDACS — the UN OCHA / EC-JRC Global Disaster Alert and Coordination System —
    is the openly-fetchable authoritative substitute used here because EM-DAT and
    ReliefWeb now gate their bulk data (login / approved-appname). For each real
    DECLARED Indian flood and tropical-cyclone event in the window we record the
    GLIDE id, dates, GDACS alert level (Green/Orange/Red — an authoritative
    severity classification), alert score and severity text (cyclone wind speed).
    This is an INDEPENDENT outcome track: it is never a model input, only a real
    yardstick the model's risk is cross-checked against (see validation.external).
    """
    events: list[dict] = []
    for etype in ("FL", "TC"):
        data = _get_json(
            _GDACS_API,
            {
                "eventtype": etype,
                "country": "India",
                "fromdate": GDACS_START,
                "todate": GDACS_END,
            },
        )
        for feat in data.get("features", []):
            p = feat.get("properties", {})
            sev = p.get("severitydata") or {}
            events.append(
                {
                    "eventtype": etype,
                    "eventid": p.get("eventid"),
                    "name": p.get("eventname") or p.get("name"),
                    "glide": p.get("glide") or "",
                    "fromdate": (p.get("fromdate") or "")[:10],
                    "todate": (p.get("todate") or "")[:10],
                    "alertlevel": p.get("alertlevel"),
                    "alertscore": p.get("alertscore"),
                    "severity": sev.get("severity"),
                    "severitytext": sev.get("severitytext"),
                }
            )
        print(f"gdacs: {etype}: {len(events)} cumulative events", file=sys.stderr)

    fixture = {
        "source": {
            "name": "GDACS — Global Disaster Alert and Coordination System (UN OCHA / EC-JRC)",
            "note": "Openly-fetchable authoritative substitute; EM-DAT and "
            "ReliefWeb gate bulk data (login / approved appname). Alert level is a "
            "severity classification, not a casualty count — used as an INDEPENDENT "
            "outcome yardstick, never as a model input.",
            "window": [GDACS_START, GDACS_END],
            "url": "https://www.gdacs.org",
        },
        "events": events,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, separators=(",", ":"))
    return fixture


# --------------------------------------------------------------- fire (INDIA, FIRMS)
#: Fire window for India (VIIRS-SNPP coverage is solid 2012+; use a 5-year span
#: with a clean temporal split: train 2019-2021, test 2022-2023).
FIRE_IN_START, FIRE_IN_END = "2015-01-01", "2024-12-31"
_FIRMS_VIIRS = "https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/{year}/viirs-snpp_{year}_India.csv"

#: 10 real fire-prone Indian cells across the dry-deciduous forest belt + the
#: Himalayan foothills — India's fire season is Feb-May (not the US summer), and
#: these regions dominate FSI's annual fire alerts. Regions tag cells into blocks
#: for leave-one-region-out CV.
FIRE_CELLS_INDIA: tuple[FireCell, ...] = (
    FireCell("kanha-mp", "MP", "central", 22.30, 80.60),
    FireCell("bastar-cg", "CG", "central", 19.10, 82.00),
    FireCell("simlipal-od", "OD", "east", 21.60, 86.30),
    FireCell("saranda-jh", "JH", "east", 22.10, 85.40),
    FireCell("vidarbha-mh", "MH", "central", 20.70, 79.50),
    FireCell("eastern-ghats-ap", "AP", "south", 18.00, 82.50),
    FireCell("nilgiris-tn", "TN", "south", 11.50, 76.70),
    FireCell("uttarakhand", "UK", "himalaya", 30.00, 79.00),
    FireCell("himachal", "HP", "himalaya", 31.50, 77.30),
    FireCell("mizoram-ne", "MZ", "northeast", 23.40, 92.80),
)


def _get_text(url: str, *, retries: int = 4) -> str:
    """GET ``url`` as text (FIRMS CSV), with linear-backoff retries."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180, context=_ssl_context()) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}") from last


def fetch_fire_india(out_path: str = FIRE_INDIA_FIXTURE) -> dict:
    """Build a REAL India fire fixture: NASA FIRMS (VIIRS-SNPP) detections + ERA5.

    Replaces the Pacific-NW FPA-FOD validation with genuine Indian geography and
    fire season. FIRMS gives lat/lon/date/confidence/FRP per detection (no fire
    'size' — FRP, fire radiative power, is the intensity proxy). One record per
    :data:`FIRE_CELLS_INDIA` cell: ERA5 daily fire weather + the real detections
    (nominal/high confidence) that fell inside the cell box.
    """
    import csv as _csv
    import io as _io

    # download all VIIRS-India detections for the window once, then bin per cell
    all_det: list[dict] = []
    years_ok: list[int] = []
    for year in range(int(FIRE_IN_START[:4]), int(FIRE_IN_END[:4]) + 1):
        try:
            text = _get_text(_FIRMS_VIIRS.format(year=year))
        except Exception as exc:  # a not-yet-archived recent year must not kill the run
            print(f"fire-india: VIIRS {year}: SKIPPED ({type(exc).__name__})", file=sys.stderr)
            continue
        years_ok.append(year)
        rdr = _csv.DictReader(_io.StringIO(text))
        kept = 0
        for row in rdr:
            conf = (row.get("confidence") or "").strip().lower()
            if conf == "l":  # drop low-confidence detections
                continue
            try:
                all_det.append(
                    {
                        "lat": float(row["latitude"]),
                        "lon": float(row["longitude"]),
                        "date": row["acq_date"],
                        "frp": float(row.get("frp") or 0.0),
                    }
                )
                kept += 1
            except (KeyError, ValueError):
                continue
        print(f"fire-india: VIIRS {year}: +{kept} detections ({len(all_det)} total)", file=sys.stderr)

    cells = []
    for cell in FIRE_CELLS_INDIA:
        weather = _get_json(
            _ARCHIVE_API,
            {
                "latitude": cell.lat,
                "longitude": cell.lon,
                "daily": (
                    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                    "wind_speed_10m_max,relative_humidity_2m_min"
                ),
                "start_date": FIRE_IN_START,
                "end_date": FIRE_IN_END,
                "timezone": "UTC",
            },
        )
        daily = weather["daily"]
        fires = [
            {"date": d["date"], "frp": round(d["frp"], 1)}
            for d in all_det
            if abs(d["lat"] - cell.lat) <= cell.half_deg
            and abs(d["lon"] - cell.lon) <= cell.half_deg
        ]
        cells.append(
            {
                **asdict(cell),
                "start": daily["time"][0],
                "tmax": _round_list(daily["temperature_2m_max"], 1),
                "tmin": _round_list(daily["temperature_2m_min"], 1),
                "precip": _round_list(daily["precipitation_sum"], 1),
                "wind_max": _round_list(daily["wind_speed_10m_max"], 1),
                "rh_min": _round_list(daily["relative_humidity_2m_min"], 0),
                "fires": fires,
            }
        )
        print(f"fire-india: {cell.name}: {len(fires)} real detections", file=sys.stderr)

    fixture = {
        "source": {
            "fires": "NASA FIRMS VIIRS-SNPP active-fire detections, India country "
            "archive (nominal/high confidence). FRP = fire radiative power (intensity).",
            "weather": "ERA5 daily fire weather (Open-Meteo historical archive)",
            "window": [FIRE_IN_START, FIRE_IN_END],
            "license": "open data, free APIs, no key",
        },
        "cells": cells,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, separators=(",", ":"))
    return fixture


def main(argv: list[str] | None = None) -> int:
    targets = {
        "flood": fetch_flood,
        "fire": fetch_fire,
        "fire-india": fetch_fire_india,
        "quake": fetch_quake,
        "gdacs": fetch_gdacs,
    }
    args = (argv if argv is not None else sys.argv[1:]) or list(targets)
    for name in args:
        if name not in targets:
            print(f"unknown target {name!r}; choose from {sorted(targets)}", file=sys.stderr)
            return 2
        targets[name]()  # type: ignore[operator]
        print(f"{name}: fixture written", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - manual, network-using entry point
    raise SystemExit(main())
