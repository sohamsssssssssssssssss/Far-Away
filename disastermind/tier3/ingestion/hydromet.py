"""Hydro-meteorological feed adapters (PRD Step 2, Module A).

  * :class:`CWCFeedAgent`   — CWC India-WRIS river gauges. Activation: water
                              level >= **75 % of the danger level** (PRD Step 1)
                              mints a FLOOD event + ALERT.
  * :class:`IMDFeedAgent`    — India Meteorological Dept cyclone bulletins &
                              heavy-rainfall warnings → CYCLONE event on a
                              named system or rainfall >= 115 mm/24h (red).
  * :class:`BhuvanFeedAgent` — ISRO Bhuvan flood-inundation polygons → FLOOD
                              event when inundated area is significant.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from ...core.contracts import Module, Priority, utcnow_iso
from ...models.domain import DisasterEvent, EventKind
from ...models.geo import LatLon
from .base import BaseFeedAgent

log = logging.getLogger("disastermind.ingestion.hydromet")

#: PRD Step 1, Module A — river-gauge activation fraction of danger level.
GAUGE_DANGER_FRACTION = 0.75
#: IMD red-warning 24h rainfall threshold (mm) — "extremely heavy".
IMD_RED_RAINFALL_MM = 115.0
#: Bhuvan inundation activation area (km^2).
BHUVAN_INUNDATION_KM2 = 5.0


def _flood_event(
    incident_id: str,
    lat: float,
    lon: float,
    severity: float,
    source: str,
    detected_at: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
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


class CWCFeedAgent(BaseFeedAgent):
    """CWC India-WRIS river-gauge telemetry (PRD Step 2, Module A).

    Each station reports current water level vs its danger & warning levels.
    A station at or above 75 % of danger level breaches activation (PRD Step 1).
    """

    feed_name = "cwc_wris"
    module = Module.CYCLONE_FLOOD

    # ----------------------------------------------------------------- parse
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Decode WRIS station records, computing the danger-level fraction."""
        stations = raw if isinstance(raw, list) else (raw or {}).get("stations", [])
        out: list[dict[str, Any]] = []
        for s in stations or []:
            danger = float(s.get("danger_level_m", 0.0) or 0.0)
            level = float(s.get("water_level_m", 0.0) or 0.0)
            frac = (level / danger) if danger > 0 else 0.0
            out.append(
                {
                    "station_id": s.get("station_id") or s.get("id") or "cwc-unknown",
                    "river": s.get("river", ""),
                    "name": s.get("name", ""),
                    "lat": float(s.get("lat", 0.0) or 0.0),
                    "lon": float(s.get("lon", 0.0) or 0.0),
                    "water_level_m": level,
                    "danger_level_m": danger,
                    "warning_level_m": float(s.get("warning_level_m", 0.0) or 0.0),
                    "danger_fraction": round(frac, 4),
                    "trend": s.get("trend", "steady"),
                }
            )
        return out

    # ---------------------------------------------------------------- sample
    def sample(self) -> list[dict[str, Any]]:
        """Offline WRIS fixture: one station above danger, one below."""
        return [
            {
                "station_id": "WRIS-GHGT-01",
                "river": "Brahmaputra",
                "name": "Guwahati",
                "lat": 26.18,
                "lon": 91.75,
                "water_level_m": 49.8,
                "danger_level_m": 49.68,
                "warning_level_m": 48.5,
                "trend": "rising",
            },
            {
                "station_id": "WRIS-PTNA-07",
                "river": "Ganga",
                "name": "Patna",
                "lat": 25.61,
                "lon": 85.14,
                "water_level_m": 46.1,
                "danger_level_m": 50.45,
                "warning_level_m": 49.2,
                "trend": "steady",
            },
        ]

    # ----------------------------------------------------------------- fetch
    def fetch(self, transport: Any = None) -> list[dict[str, Any]]:  # pragma: no cover - network path
        """Live GET of the India-WRIS gauge API via the shared HTTP transport."""
        from .http import http_get_json

        url = "https://indiawris.gov.in/wris/api/RiverMonitoring/getRiverStations"
        try:
            data = http_get_json(url, timeout=10.0, transport=transport)
            return data if isinstance(data, list) else data.get("stations", [])
        except Exception:
            log.exception("CWC-WRIS fetch failed; using sample()")
            return self.sample()

    # ---------------------------------------------------------------- assess
    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        """Flag stations >= 75 % of danger level (PRD Step 1, Module A)."""
        breaches = [o for o in observations if o["danger_fraction"] >= GAUGE_DANGER_FRACTION]
        if not breaches:
            top = max((o["danger_fraction"] for o in observations), default=0.0)
            return False, Priority.INFO, [
                f"cwc: max gauge {top:.0%} of danger level below 75% activation"
            ]
        worst = max(breaches, key=lambda o: o["danger_fraction"])
        # >=100% of danger level is critical; 75-100% high.
        prio = Priority.CRITICAL if worst["danger_fraction"] >= 1.0 else Priority.HIGH
        return True, prio, [
            f"cwc: {len(breaches)} gauge(s) >= 75% of danger level (PRD Step 1, Module A)",
            f"worst {worst['name']} ({worst['river']}) at {worst['danger_fraction']:.0%}, "
            f"trend {worst['trend']}",
        ]

    # ----------------------------------------------------------------- event
    def build_event(self, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        breaches = [o for o in observations if o["danger_fraction"] >= GAUGE_DANGER_FRACTION]
        if not breaches:
            return None
        worst = max(breaches, key=lambda o: o["danger_fraction"])
        return _flood_event(
            incident_id=f"cwc:{worst['station_id']}",
            lat=worst["lat"],
            lon=worst["lon"],
            severity=round(worst["danger_fraction"], 3),
            source="CWC-WRIS",
            detected_at=utcnow_iso(),
            meta={
                "river": worst["river"],
                "station": worst["name"],
                "water_level_m": worst["water_level_m"],
                "danger_level_m": worst["danger_level_m"],
                "danger_fraction": worst["danger_fraction"],
                "trend": worst["trend"],
            },
        )


class IMDFeedAgent(BaseFeedAgent):
    """IMD cyclone bulletins & heavy-rainfall warnings (PRD Step 2, Module A).

    A named cyclonic system, or 24h rainfall >= 115 mm (red category) breaches
    activation and mints a CYCLONE event.
    """

    feed_name = "imd"
    module = Module.CYCLONE_FLOOD

    # ----------------------------------------------------------------- parse
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Decode IMD bulletins (cyclone systems + rainfall warnings)."""
        bulletins = raw if isinstance(raw, list) else (raw or {}).get("bulletins", [])
        out: list[dict[str, Any]] = []
        for b in bulletins or []:
            out.append(
                {
                    "bulletin_id": b.get("bulletin_id") or b.get("id") or "imd-unknown",
                    "type": b.get("type", "rainfall"),  # cyclone | rainfall
                    "system_name": b.get("system_name", ""),
                    "category": b.get("category", ""),  # e.g. "Severe Cyclonic Storm"
                    "warning_colour": str(b.get("warning_colour", "")).lower(),
                    "lat": float(b.get("lat", 0.0) or 0.0),
                    "lon": float(b.get("lon", 0.0) or 0.0),
                    "max_wind_kmph": float(b.get("max_wind_kmph", 0.0) or 0.0),
                    "rainfall_mm_24h": float(b.get("rainfall_mm_24h", 0.0) or 0.0),
                    "region": b.get("region", ""),
                }
            )
        return out

    # ---------------------------------------------------------------- sample
    def sample(self) -> list[dict[str, Any]]:
        """Offline IMD fixture: a severe cyclonic storm + a red rainfall warning."""
        return [
            {
                "bulletin_id": "IMD-BOB-2026-07",
                "type": "cyclone",
                "system_name": "Cyclone Aarambh",
                "category": "Severe Cyclonic Storm",
                "warning_colour": "red",
                "lat": 19.30,
                "lon": 86.80,
                "max_wind_kmph": 130.0,
                "rainfall_mm_24h": 95.0,
                "region": "Odisha coast (Bay of Bengal)",
            },
            {
                "bulletin_id": "IMD-RAIN-2026-441",
                "type": "rainfall",
                "warning_colour": "red",
                "lat": 19.07,
                "lon": 72.88,
                "max_wind_kmph": 40.0,
                "rainfall_mm_24h": 168.0,
                "region": "Mumbai",
            },
        ]

    # ----------------------------------------------------------------- fetch
    def fetch(self, transport: Any = None) -> list[dict[str, Any]]:  # pragma: no cover - network path
        """Live GET of IMD bulletins via the shared HTTP transport."""
        from .http import http_get_json

        base = getattr(self.settings, "imd_base_url", None) or "https://mausam.imd.gov.in"
        url = f"{base}/api/cyclone_warning.php"
        try:
            data = http_get_json(url, timeout=10.0, transport=transport)
            return data if isinstance(data, list) else data.get("bulletins", [])
        except Exception:
            log.exception("IMD fetch failed; using sample()")
            return self.sample()

    # ---------------------------------------------------------------- assess
    @staticmethod
    def _is_breach(o: dict[str, Any]) -> bool:
        if o["type"] == "cyclone" and o["system_name"]:
            return True
        if o["warning_colour"] in {"red", "orange"}:
            return True
        return o["rainfall_mm_24h"] >= IMD_RED_RAINFALL_MM

    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        breaches = [o for o in observations if self._is_breach(o)]
        if not breaches:
            return False, Priority.INFO, ["imd: no active cyclone / red-rainfall warning"]
        has_red = any(o["warning_colour"] == "red" or o["type"] == "cyclone" for o in breaches)
        prio = Priority.CRITICAL if has_red else Priority.HIGH
        names = ", ".join(
            sorted({o["system_name"] or o["region"] or o["bulletin_id"] for o in breaches})
        )
        return True, prio, [
            f"imd: {len(breaches)} active warning(s) (PRD Step 1, Module A): {names}",
        ]

    # ----------------------------------------------------------------- event
    def build_event(self, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        breaches = [o for o in observations if self._is_breach(o)]
        if not breaches:
            return None
        # Prefer a named cyclone system; else the heaviest-rainfall bulletin.
        cyclones = [o for o in breaches if o["type"] == "cyclone" and o["system_name"]]
        chosen = (
            max(cyclones, key=lambda o: o["max_wind_kmph"])
            if cyclones
            else max(breaches, key=lambda o: o["rainfall_mm_24h"])
        )
        kind = EventKind.CYCLONE if chosen["type"] == "cyclone" else EventKind.FLOOD
        # Severity: cyclone -> wind (kmph); rainfall -> mm/24h.
        severity = chosen["max_wind_kmph"] if kind == EventKind.CYCLONE else chosen["rainfall_mm_24h"]
        ev = DisasterEvent(
            incident_id=f"imd:{chosen['bulletin_id']}",
            kind=kind,
            epicentre=LatLon(chosen["lat"], chosen["lon"]),
            severity=severity,
            detected_at=utcnow_iso(),
            source="IMD",
            meta={
                "system_name": chosen["system_name"],
                "category": chosen["category"],
                "warning_colour": chosen["warning_colour"],
                "max_wind_kmph": chosen["max_wind_kmph"],
                "rainfall_mm_24h": chosen["rainfall_mm_24h"],
                "region": chosen["region"],
            },
        )
        d = asdict(ev)
        d["kind"] = ev.kind.value
        return d


class BhuvanFeedAgent(BaseFeedAgent):
    """ISRO Bhuvan flood-inundation mapping (PRD Step 2, Module A).

    Bhuvan publishes satellite-derived inundation footprints. A footprint over
    :data:`BHUVAN_INUNDATION_KM2` km^2 breaches activation and mints a FLOOD
    event centred on the footprint.
    """

    feed_name = "bhuvan"
    module = Module.CYCLONE_FLOOD

    # ----------------------------------------------------------------- parse
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Decode Bhuvan inundation footprints (area + centroid)."""
        feats = raw if isinstance(raw, list) else (raw or {}).get("inundation", [])
        out: list[dict[str, Any]] = []
        for f in feats or []:
            out.append(
                {
                    "footprint_id": f.get("footprint_id") or f.get("id") or "bhuvan-unknown",
                    "district": f.get("district", ""),
                    "lat": float(f.get("lat", 0.0) or 0.0),
                    "lon": float(f.get("lon", 0.0) or 0.0),
                    "inundated_km2": float(f.get("inundated_km2", 0.0) or 0.0),
                    "observed_at": f.get("observed_at", ""),
                    "satellite": f.get("satellite", ""),
                }
            )
        return out

    # ---------------------------------------------------------------- sample
    def sample(self) -> list[dict[str, Any]]:
        """Offline Bhuvan fixture: a large inundation footprint + a minor one."""
        return [
            {
                "footprint_id": "BHUVAN-ASM-2026-12",
                "district": "Barpeta, Assam",
                "lat": 26.32,
                "lon": 91.00,
                "inundated_km2": 42.7,
                "observed_at": "2026-06-08T05:30:00Z",
                "satellite": "RISAT-1A",
            },
            {
                "footprint_id": "BHUVAN-ASM-2026-13",
                "district": "Nalbari, Assam",
                "lat": 26.45,
                "lon": 91.44,
                "inundated_km2": 1.2,
                "observed_at": "2026-06-08T05:30:00Z",
                "satellite": "RISAT-1A",
            },
        ]

    # ----------------------------------------------------------------- fetch
    def fetch(self, transport: Any = None) -> list[dict[str, Any]]:  # pragma: no cover - network path
        """Live GET of Bhuvan flood-services WFS/JSON via the shared transport.

        Accepts the injectable ``transport`` seam (like the other live adapters)
        so live polling conforms to the base contract — no signature mismatch —
        and tests can drive it offline. Any failure degrades to ``sample()``.
        """
        from .http import http_get_json

        url = "https://bhuvan-app1.nrsc.gov.in/api/flood/inundation.json"
        try:
            data = http_get_json(url, timeout=15.0, transport=transport)
            return data if isinstance(data, list) else data.get("inundation", [])
        except Exception:
            log.exception("Bhuvan fetch failed; using sample()")
            return self.sample()

    # ---------------------------------------------------------------- assess
    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        breaches = [o for o in observations if o["inundated_km2"] >= BHUVAN_INUNDATION_KM2]
        if not breaches:
            top = max((o["inundated_km2"] for o in observations), default=0.0)
            return False, Priority.INFO, [
                f"bhuvan: max inundation {top:.1f} km2 below {BHUVAN_INUNDATION_KM2} km2 activation"
            ]
        worst = max(breaches, key=lambda o: o["inundated_km2"])
        total = sum(o["inundated_km2"] for o in breaches)
        prio = Priority.CRITICAL if total >= 50.0 else Priority.HIGH
        return True, prio, [
            f"bhuvan: {len(breaches)} footprint(s) >= {BHUVAN_INUNDATION_KM2} km2 (PRD Step 1)",
            f"worst {worst['district']} {worst['inundated_km2']:.1f} km2; total {total:.1f} km2",
        ]

    # ----------------------------------------------------------------- event
    def build_event(self, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        breaches = [o for o in observations if o["inundated_km2"] >= BHUVAN_INUNDATION_KM2]
        if not breaches:
            return None
        worst = max(breaches, key=lambda o: o["inundated_km2"])
        return _flood_event(
            incident_id=f"bhuvan:{worst['footprint_id']}",
            lat=worst["lat"],
            lon=worst["lon"],
            severity=round(worst["inundated_km2"], 2),
            source="ISRO-Bhuvan",
            detected_at=utcnow_iso(),
            meta={
                "district": worst["district"],
                "inundated_km2": worst["inundated_km2"],
                "satellite": worst["satellite"],
                "observed_at": worst["observed_at"],
            },
        )
