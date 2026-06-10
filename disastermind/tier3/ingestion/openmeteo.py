"""Open-Meteo forecast feed adapter (PRD Step 2, Module A).

Open-Meteo is a free, no-API-key forecast service. :class:`OpenMeteoFeedAgent`
pulls the hourly forecast (precipitation, wind speed/gusts, and — where
available — thunderstorm probability) for a configured AOI and reports it so the
Tier 2 prediction model can refine its cyclone/flood outlook.

Activation (PRD Step 1, Module A): an hour whose precipitation reaches the IMD
red-rainfall rate, whose wind reaches gale force, or whose storm probability is
high breaches threshold and mints a FLOOD :class:`DisasterEvent` centred on the
forecast point. Routine forecasts ride as informational observations (Tier 3
observes & reports only — no decision authority).
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from ...core.contracts import Module, Priority, utcnow_iso
from ...models.domain import DisasterEvent, EventKind
from ...models.geo import LatLon
from .base import BaseFeedAgent

log = logging.getLogger("disastermind.ingestion.openmeteo")

#: PRD Step 1, Module A — hourly precipitation rate (mm/h) flagged as heavy.
OPENMETEO_PRECIP_MM_H = 20.0
#: Gale-force sustained wind (km/h) that materially raises cyclone/flood risk.
OPENMETEO_WIND_KMH = 62.0
#: Thunderstorm probability (percent) treated as a high-confidence storm signal.
OPENMETEO_STORM_PCT = 60.0


def _flood_event(
    incident_id: str,
    lat: float,
    lon: float,
    severity: float,
    source: str,
    detected_at: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Build a JSON-able FLOOD :class:`DisasterEvent` dict (Module A)."""
    ev = DisasterEvent(
        incident_id=incident_id,
        kind=EventKind.FLOOD,
        epicentre=LatLon(lat, lon),
        severity=severity,
        detected_at=detected_at,
        source=source,
        meta=meta,
    )
    d = asdict(ev)
    d["kind"] = ev.kind.value
    return d


class OpenMeteoFeedAgent(BaseFeedAgent):
    """Open-Meteo hourly forecast (PRD Step 2, Module A).

    Open-Meteo returns parallel hourly arrays keyed by ``time`` under an
    ``hourly`` object plus a top-level ``latitude``/``longitude``. We flatten
    them into one observation per hour, attaching the AOI coordinates so each
    observation is self-describing and :meth:`parse` stays pure.
    """

    feed_name = "open_meteo"
    module = Module.CYCLONE_FLOOD

    # ----------------------------------------------------------------- parse
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Flatten Open-Meteo parallel hourly arrays into observation dicts."""
        if not isinstance(raw, dict):
            return []
        lat = float(raw.get("latitude", 0.0) or 0.0)
        lon = float(raw.get("longitude", 0.0) or 0.0)
        hourly = raw.get("hourly", {}) or {}
        times = hourly.get("time", []) or []
        precip = hourly.get("precipitation", []) or []
        wind = hourly.get("wind_speed_10m", []) or []
        gust = hourly.get("wind_gusts_10m", []) or []
        storm = hourly.get("thunderstorm_probability", hourly.get("precipitation_probability", []))
        storm = storm or []

        def _at(seq: list[Any], i: int) -> float:
            return float(seq[i]) if i < len(seq) and seq[i] is not None else 0.0

        out: list[dict[str, Any]] = []
        for i, t in enumerate(times):
            out.append(
                {
                    "time": t,
                    "lat": lat,
                    "lon": lon,
                    "precip_mm_h": _at(precip, i),
                    "wind_kmh": _at(wind, i),
                    "gust_kmh": _at(gust, i),
                    "storm_pct": _at(storm, i),
                }
            )
        return out

    # ---------------------------------------------------------------- sample
    def sample(self) -> dict[str, Any]:
        """Offline Open-Meteo fixture: a 3-hour Odisha-coast forecast (1 severe)."""
        return {
            "latitude": 19.31,
            "longitude": 86.61,
            "timezone": "Asia/Kolkata",
            "hourly_units": {
                "precipitation": "mm",
                "wind_speed_10m": "km/h",
                "wind_gusts_10m": "km/h",
                "thunderstorm_probability": "%",
            },
            "hourly": {
                "time": [
                    "2026-06-08T06:00",
                    "2026-06-08T07:00",
                    "2026-06-08T08:00",
                ],
                "precipitation": [3.4, 28.6, 9.1],
                "wind_speed_10m": [41.0, 74.5, 58.0],
                "wind_gusts_10m": [60.0, 102.0, 80.0],
                "thunderstorm_probability": [40.0, 85.0, 55.0],
            },
        }

    # ----------------------------------------------------------------- fetch
    #: Default forecast AOI (Odisha coast, Bay of Bengal — Module A demo).
    DEFAULT_LAT = 19.31
    DEFAULT_LON = 86.61
    DEFAULT_BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def build_url(self) -> str:
        """Construct the Open-Meteo forecast request URL (free, no API key)."""
        base = (
            getattr(self.settings, "open_meteo_base_url", None)
            or getattr(self.settings, "openmeteo_url", None)
            or self.DEFAULT_BASE_URL
        )
        lat = getattr(self.settings, "open_meteo_lat", None) or self.DEFAULT_LAT
        lon = getattr(self.settings, "open_meteo_lon", None) or self.DEFAULT_LON
        return (
            f"{base}?latitude={lat}&longitude={lon}"
            "&hourly=precipitation,wind_speed_10m,wind_gusts_10m,precipitation_probability"
            "&forecast_days=1"
        )

    def fetch(self, transport: Any = None) -> dict[str, Any]:
        """Live GET of the Open-Meteo forecast API (free, no key).

        Uses the shared HTTP transport (lazy ``httpx`` with a stdlib
        ``urllib.request`` fallback — no hard dependency). ``transport`` is
        injected only by tests with a recorded fixture; production passes
        ``None``. Any failure degrades to :meth:`sample` (PRD Step 10).
        """
        from .http import http_get_json

        url = self.build_url()
        try:
            return http_get_json(url, timeout=10.0, transport=transport)
        except Exception:
            log.exception("Open-Meteo fetch failed; using sample()")
            return self.sample()

    # ---------------------------------------------------------------- assess
    @staticmethod
    def _is_breach(o: dict[str, Any]) -> bool:
        return (
            o["precip_mm_h"] >= OPENMETEO_PRECIP_MM_H
            or o["wind_kmh"] >= OPENMETEO_WIND_KMH
            or o["storm_pct"] >= OPENMETEO_STORM_PCT
        )

    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        """Flag heavy-rain / gale / high-storm-probability hours (PRD Step 1)."""
        breaches = [o for o in observations if self._is_breach(o)]
        if not breaches:
            peak = max((o["precip_mm_h"] for o in observations), default=0.0)
            return False, Priority.INFO, [
                f"open_meteo: peak precip {peak:.1f} mm/h below "
                f"{OPENMETEO_PRECIP_MM_H:.0f} mm/h activation"
            ]
        worst = max(breaches, key=lambda o: (o["precip_mm_h"], o["wind_kmh"]))
        # Both heavy rain AND gale wind in the same hour is critical.
        prio = (
            Priority.CRITICAL
            if worst["precip_mm_h"] >= OPENMETEO_PRECIP_MM_H
            and worst["wind_kmh"] >= OPENMETEO_WIND_KMH
            else Priority.HIGH
        )
        return True, prio, [
            f"open_meteo: {len(breaches)} severe forecast hour(s) (PRD Step 1, Module A)",
            f"worst {worst['time']}: {worst['precip_mm_h']:.1f} mm/h, "
            f"wind {worst['wind_kmh']:.0f} km/h, storm {worst['storm_pct']:.0f}%",
        ]

    # ----------------------------------------------------------------- event
    def build_event(self, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        breaches = [o for o in observations if self._is_breach(o)]
        if not breaches:
            return None
        worst = max(breaches, key=lambda o: (o["precip_mm_h"], o["wind_kmh"]))
        return _flood_event(
            incident_id=f"open_meteo:{worst['lat']:.2f},{worst['lon']:.2f}@{worst['time']}",
            lat=worst["lat"],
            lon=worst["lon"],
            severity=round(worst["precip_mm_h"], 2),
            source="Open-Meteo",
            detected_at=utcnow_iso(),
            meta={
                "forecast_time": worst["time"],
                "precip_mm_h": worst["precip_mm_h"],
                "wind_kmh": worst["wind_kmh"],
                "gust_kmh": worst["gust_kmh"],
                "storm_pct": worst["storm_pct"],
                "severe_hours": len(breaches),
            },
        )
