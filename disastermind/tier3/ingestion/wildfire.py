"""Fire-spread feed adapters (PRD Step 2, Module C).

  * :class:`FIRMSFeedAgent` — NASA FIRMS active-fire detections (VIIRS/MODIS).
                              Activation: a detection whose brightness
                              temperature and confidence both clear the alert
                              thresholds (PRD Step 1, Module C) mints an
                              URBAN_FIRE event + ALERT.
  * :class:`OpenWeatherMapFeedAgent` — OpenWeatherMap current-conditions wind
                              speed/direction. Wind feeds the fire-spread model
                              (Tier 2); a gale-force wind near an active fire is
                              flagged HIGH so prediction can widen the front.

Both adapters observe & report only (Tier 3, no decision authority). They parse
provider-native JSON, ship a realistic offline ``sample()`` fixture, and fetch
live via a lazily-imported ``httpx`` that always degrades to ``sample()`` on
failure (PRD Step 10, graceful degradation).
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from ...core.contracts import Module, Priority, utcnow_iso
from ...models.domain import DisasterEvent, EventKind
from ...models.geo import LatLon
from .base import BaseFeedAgent

log = logging.getLogger("disastermind.ingestion.wildfire")

#: PRD Step 1, Module C — FIRMS active-fire brightness temperature (Kelvin).
FIRMS_BRIGHTNESS_K = 330.0
#: PRD Step 1, Module C — FIRMS detection confidence floor (percent or
#: nominal/high label) to suppress noise / false positives.
FIRMS_CONFIDENCE_PCT = 50.0
#: A clearly-elevated brightness temperature → critical fire intensity (Kelvin).
FIRMS_BRIGHTNESS_CRITICAL_K = 360.0
#: Wind speed (m/s) above which fire spread accelerates materially (~ gale).
OWM_WIND_HIGH_MS = 14.0


def _fire_event(
    incident_id: str,
    lat: float,
    lon: float,
    severity: float,
    source: str,
    detected_at: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Build a JSON-able URBAN_FIRE :class:`DisasterEvent` dict (Module C)."""
    ev = DisasterEvent(
        incident_id=incident_id,
        kind=EventKind.URBAN_FIRE,
        epicentre=LatLon(lat, lon),
        severity=severity,
        detected_at=detected_at,
        source=source,
        meta=meta,
    )
    d = asdict(ev)
    d["kind"] = ev.kind.value
    return d


def _confidence_pct(value: Any) -> float:
    """Coerce FIRMS confidence (numeric % or l/n/h label) to a percentage.

    VIIRS reports a nominal/low/high label; MODIS reports a 0-100 integer.
    Normalising here keeps :meth:`FIRMSFeedAgent.parse` pure and provider-agnostic.
    """
    if isinstance(value, (int, float)):
        return float(value)
    label = str(value or "").strip().lower()
    return {"l": 20.0, "low": 20.0, "n": 60.0, "nominal": 60.0, "h": 90.0, "high": 90.0}.get(
        label, 0.0
    )


class FIRMSFeedAgent(BaseFeedAgent):
    """NASA FIRMS active-fire detections (PRD Step 2, Module C).

    FIRMS distributes VIIRS/MODIS thermal anomalies as CSV/JSON rows carrying
    a latitude/longitude, brightness temperature, scan confidence and FRP (fire
    radiative power). A detection above :data:`FIRMS_BRIGHTNESS_K` with
    confidence >= :data:`FIRMS_CONFIDENCE_PCT` breaches activation and mints an
    URBAN_FIRE event centred on the hottest pixel (PRD Step 1, Module C).
    """

    feed_name = "firms"
    module = Module.FIRE_COLLAPSE

    # ----------------------------------------------------------------- parse
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Decode FIRMS detection rows into normalised observation dicts."""
        rows = raw if isinstance(raw, list) else (raw or {}).get("detections", [])
        out: list[dict[str, Any]] = []
        for r in rows or []:
            lat = float(r.get("latitude", r.get("lat", 0.0)) or 0.0)
            lon = float(r.get("longitude", r.get("lon", 0.0)) or 0.0)
            # FIRMS uses ``bright_ti4`` (VIIRS) or ``brightness`` (MODIS).
            bright = r.get("bright_ti4", r.get("brightness", r.get("bright_t31")))
            if bright is None:
                continue
            out.append(
                {
                    "id": r.get("id")
                    or f"firms:{lat:.4f},{lon:.4f}@{r.get('acq_time', '')}",
                    "lat": lat,
                    "lon": lon,
                    "brightness_k": float(bright),
                    "confidence_pct": _confidence_pct(r.get("confidence")),
                    "frp_mw": float(r.get("frp", 0.0) or 0.0),
                    "satellite": r.get("satellite", ""),
                    "daynight": r.get("daynight", ""),
                    "acq_date": r.get("acq_date", ""),
                    "acq_time": str(r.get("acq_time", "")),
                }
            )
        return out

    # ---------------------------------------------------------------- sample
    def sample(self) -> list[dict[str, Any]]:
        """Offline FIRMS fixture: one hot high-confidence pixel + one weak one."""
        return [
            {
                "id": "firms-2026-0608-01",
                "latitude": 28.6139,
                "longitude": 77.2090,
                "bright_ti4": 364.5,
                "confidence": "high",
                "frp": 48.2,
                "satellite": "N20",
                "daynight": "D",
                "acq_date": "2026-06-08",
                "acq_time": "0712",
            },
            {
                "id": "firms-2026-0608-02",
                "latitude": 19.0760,
                "longitude": 72.8777,
                "bright_ti4": 318.0,
                "confidence": "low",
                "frp": 4.1,
                "satellite": "N20",
                "daynight": "D",
                "acq_date": "2026-06-08",
                "acq_time": "0712",
            },
        ]

    # ----------------------------------------------------------------- fetch
    #: NASA FIRMS area-API base (key-gated source — PRD Step 2).
    DEFAULT_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area"

    @staticmethod
    def parse_csv(text: str) -> list[dict[str, Any]]:
        """Coerce a FIRMS area-API CSV body into a list of row dicts (stdlib).

        Pure and testable against the committed ``firms_viirs.csv`` fixture so
        the live CSV shape is validated without any network call.
        """
        import csv as _csv
        import io as _io

        reader = _csv.DictReader(_io.StringIO(text))
        return [dict(row) for row in reader]

    def fetch(self, transport: Any = None) -> list[dict[str, Any]]:
        """Live GET of the NASA FIRMS area API — key-gated (PRD Step 2).

        FIRMS requires a MAP_KEY (``settings.firms_api_key``). Without it the
        adapter never touches the network and degrades to :meth:`sample` so the
        edge node stays offline-safe. FIRMS returns CSV, decoded by
        :meth:`parse_csv`. ``transport`` is injected only by tests; production
        passes ``None``. Any failure degrades to :meth:`sample` (PRD Step 10).
        """
        from .http import http_get_text

        key = getattr(self.settings, "firms_api_key", None) or getattr(
            self.settings, "firms_map_key", None
        )
        if not key:
            log.info("FIRMS has no MAP_KEY configured; using sample()")
            return self.sample()
        base = getattr(self.settings, "firms_base_url", None) or self.DEFAULT_BASE_URL
        url = f"{base}/csv/{key}/VIIRS_NOAA20_NRT/world/1"
        try:
            text = http_get_text(url, timeout=15.0, transport=transport)
            rows = self.parse_csv(text)
            return rows or self.sample()
        except Exception:
            log.exception("FIRMS fetch failed; using sample()")
            return self.sample()

    # ---------------------------------------------------------------- assess
    @staticmethod
    def _is_breach(o: dict[str, Any]) -> bool:
        return (
            o["brightness_k"] >= FIRMS_BRIGHTNESS_K
            and o["confidence_pct"] >= FIRMS_CONFIDENCE_PCT
        )

    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        """Flag hot, high-confidence detections (PRD Step 1, Module C)."""
        breaches = [o for o in observations if self._is_breach(o)]
        if not breaches:
            top = max((o["brightness_k"] for o in observations), default=0.0)
            return False, Priority.INFO, [
                f"firms: hottest pixel {top:.0f}K below activation "
                f"{FIRMS_BRIGHTNESS_K:.0f}K / {FIRMS_CONFIDENCE_PCT:.0f}% confidence"
            ]
        hottest = max(breaches, key=lambda o: o["brightness_k"])
        prio = (
            Priority.CRITICAL
            if hottest["brightness_k"] >= FIRMS_BRIGHTNESS_CRITICAL_K
            else Priority.HIGH
        )
        return True, prio, [
            f"firms: {len(breaches)} active-fire pixel(s) >= {FIRMS_BRIGHTNESS_K:.0f}K "
            f"(PRD Step 1, Module C)",
            f"hottest {hottest['brightness_k']:.0f}K @ ({hottest['lat']:.3f}, "
            f"{hottest['lon']:.3f}), FRP {hottest['frp_mw']:.0f} MW",
        ]

    # ----------------------------------------------------------------- event
    def build_event(self, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        breaches = [o for o in observations if self._is_breach(o)]
        if not breaches:
            return None
        hottest = max(breaches, key=lambda o: o["brightness_k"])
        return _fire_event(
            incident_id=f"firms:{hottest['id']}",
            lat=hottest["lat"],
            lon=hottest["lon"],
            severity=round(hottest["brightness_k"], 1),
            source="FIRMS",
            detected_at=utcnow_iso(),
            meta={
                "brightness_k": hottest["brightness_k"],
                "confidence_pct": hottest["confidence_pct"],
                "frp_mw": hottest["frp_mw"],
                "satellite": hottest["satellite"],
                "daynight": hottest["daynight"],
                "active_pixels": len(breaches),
            },
        )


class OpenWeatherMapFeedAgent(BaseFeedAgent):
    """OpenWeatherMap current-conditions wind (PRD Step 2, Module C).

    Wind speed and bearing drive the Tier 2 fire-spread model; this adapter
    observes & reports them. A wind at or above :data:`OWM_WIND_HIGH_MS`
    (roughly gale force) is flagged HIGH so prediction can widen the projected
    fire front, but no DisasterEvent is minted — wind alone is not a hazard
    (PRD Step 2: weather feeds report raw observations).
    """

    feed_name = "openweathermap"
    module = Module.FIRE_COLLAPSE

    # ----------------------------------------------------------------- parse
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Decode OpenWeatherMap ``/weather`` responses into wind observations."""
        items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        out: list[dict[str, Any]] = []
        for r in items or []:
            if not isinstance(r, dict):
                continue
            wind = r.get("wind", {}) or {}
            coord = r.get("coord", {}) or {}
            speed = wind.get("speed")
            if speed is None:
                continue
            out.append(
                {
                    "station_id": r.get("id") or r.get("name") or "owm-unknown",
                    "name": r.get("name", ""),
                    "lat": float(coord.get("lat", r.get("lat", 0.0)) or 0.0),
                    "lon": float(coord.get("lon", r.get("lon", 0.0)) or 0.0),
                    "wind_speed_ms": float(speed),
                    "wind_deg": float(wind.get("deg", 0.0) or 0.0),
                    "wind_gust_ms": float(wind.get("gust", 0.0) or 0.0),
                    "observed_at": int(r.get("dt", 0) or 0),
                }
            )
        return out

    # ---------------------------------------------------------------- sample
    def sample(self) -> list[dict[str, Any]]:
        """Offline OWM fixture: a strong dry wind near the FIRMS Delhi hotspot."""
        return [
            {
                "id": 1273294,
                "name": "Delhi",
                "coord": {"lat": 28.6139, "lon": 77.2090},
                "wind": {"speed": 16.4, "deg": 245, "gust": 21.0},
                "dt": 1_749_369_120,
            },
            {
                "id": 1275339,
                "name": "Mumbai",
                "coord": {"lat": 19.0760, "lon": 72.8777},
                "wind": {"speed": 5.2, "deg": 200, "gust": 7.0},
                "dt": 1_749_369_120,
            },
        ]

    # ----------------------------------------------------------------- fetch
    def fetch(self, transport: Any = None) -> list[dict[str, Any]]:
        """Live GET of OpenWeatherMap current weather — key-gated (PRD Step 2).

        OWM requires an ``appid``; without one the adapter stays offline and
        degrades to :meth:`sample`. Uses the shared HTTP transport (lazy
        ``httpx`` with stdlib ``urllib`` fallback). ``transport`` is injected
        only by tests; production passes ``None``.
        """
        from .http import http_get_json

        key = getattr(self.settings, "owm_api_key", None) or ""
        if not key:
            log.info("OpenWeatherMap has no API key configured; using sample()")
            return self.sample()
        base = getattr(self.settings, "owm_base_url", None) or (
            "https://api.openweathermap.org/data/2.5/weather"
        )
        # Default to the Module-C demo AOI (Delhi) when no coords configured.
        url = f"{base}?lat=28.6139&lon=77.2090&units=metric&appid={key}"
        try:
            data = http_get_json(url, timeout=10.0, transport=transport)
            return data if isinstance(data, list) else [data]
        except Exception:
            log.exception("OpenWeatherMap fetch failed; using sample()")
            return self.sample()

    # ---------------------------------------------------------------- assess
    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        """Flag gale-force wind that will accelerate fire spread (PRD Step 1)."""
        breaches = [o for o in observations if o["wind_speed_ms"] >= OWM_WIND_HIGH_MS]
        if not breaches:
            top = max((o["wind_speed_ms"] for o in observations), default=0.0)
            return False, Priority.INFO, [
                f"owm: peak wind {top:.1f} m/s below {OWM_WIND_HIGH_MS:.0f} m/s "
                "fire-spread threshold"
            ]
        windiest = max(breaches, key=lambda o: o["wind_speed_ms"])
        return True, Priority.HIGH, [
            f"owm: {len(breaches)} site(s) with wind >= {OWM_WIND_HIGH_MS:.0f} m/s "
            "(PRD Step 1, Module C fire spread)",
            f"strongest {windiest['wind_speed_ms']:.1f} m/s @ {windiest['name']} "
            f"bearing {windiest['wind_deg']:.0f} deg",
        ]
