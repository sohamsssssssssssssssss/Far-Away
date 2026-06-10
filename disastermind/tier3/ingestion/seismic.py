"""Seismic feed adapters — USGS GeoJSON and NCS (India) RSS (PRD Step 2).

Activation threshold (PRD Step 1, Module B): an earthquake of **magnitude
M4.5+** mints a :class:`DisasterEvent` and an ALERT. Smaller quakes are
reported as INFO so the prediction tier still sees the seismicity baseline.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from ...core.contracts import Module, Priority, utcnow_iso
from ...models.domain import DisasterEvent, EventKind
from ...models.geo import LatLon
from .base import BaseFeedAgent

log = logging.getLogger("disastermind.ingestion.seismic")

#: PRD Step 1, Module B activation magnitude.
USGS_MAGNITUDE_THRESHOLD = 4.5


def _event_dict(
    incident_id: str,
    lat: float,
    lon: float,
    magnitude: float,
    source: str,
    place: str,
    detected_at: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-able :class:`DisasterEvent` dict for an earthquake."""
    meta = {"place": place, "magnitude": magnitude}
    if extra:
        meta.update(extra)
    ev = DisasterEvent(
        incident_id=incident_id,
        kind=EventKind.EARTHQUAKE,
        epicentre=LatLon(lat, lon),
        severity=magnitude,
        detected_at=detected_at,
        source=source,
        meta=meta,
    )
    d = asdict(ev)
    d["kind"] = ev.kind.value
    return d


class USGSFeedAgent(BaseFeedAgent):
    """USGS Earthquake Hazards GeoJSON summary feed (PRD Step 2, Module B).

    Parses the standard USGS GeoJSON ``FeatureCollection`` (mag, place,
    ``[lon, lat, depth]`` geometry, event id) and flags any M4.5+ quake.
    """

    feed_name = "usgs"
    module = Module.EARTHQUAKE

    # ----------------------------------------------------------------- parse
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Decode a USGS GeoJSON FeatureCollection into observation dicts."""
        if not isinstance(raw, dict):
            return []
        out: list[dict[str, Any]] = []
        for feat in raw.get("features", []) or []:
            props = feat.get("properties", {}) or {}
            geom = feat.get("geometry", {}) or {}
            coords = geom.get("coordinates") or [0.0, 0.0, 0.0]
            mag = props.get("mag")
            if mag is None:
                continue
            lon = float(coords[0])
            lat = float(coords[1])
            depth = float(coords[2]) if len(coords) > 2 else 0.0
            # USGS `time` is epoch milliseconds; keep numeric, derive ISO lazily.
            out.append(
                {
                    "id": feat.get("id") or props.get("code") or "usgs-unknown",
                    "magnitude": float(mag),
                    "lat": lat,
                    "lon": lon,
                    "depth_km": depth,
                    "place": props.get("place", ""),
                    "time_ms": props.get("time"),
                    "tsunami": int(props.get("tsunami", 0) or 0),
                }
            )
        return out

    # ---------------------------------------------------------------- sample
    def sample(self) -> dict[str, Any]:
        """Offline USGS GeoJSON fixture (one M4.9 near Guwahati, one M2.1)."""
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "us7000sample1",
                    "properties": {
                        "mag": 4.9,
                        "place": "32 km NE of Guwahati, India",
                        "time": 1_733_600_000_000,
                        "tsunami": 0,
                        "code": "7000sample1",
                    },
                    "geometry": {"type": "Point", "coordinates": [91.95, 26.35, 18.0]},
                },
                {
                    "type": "Feature",
                    "id": "us7000sample2",
                    "properties": {
                        "mag": 2.1,
                        "place": "10 km S of Shimla, India",
                        "time": 1_733_600_500_000,
                        "tsunami": 0,
                        "code": "7000sample2",
                    },
                    "geometry": {"type": "Point", "coordinates": [77.17, 31.01, 9.0]},
                },
            ],
        }

    # ----------------------------------------------------------------- fetch
    #: Default USGS all_hour summary feed (free, no API key — PRD Step 2).
    DEFAULT_URL = (
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
    )

    def fetch(self, transport: Any = None) -> dict[str, Any]:
        """Live GET of the USGS all_hour GeoJSON feed (free, no key).

        Uses the shared HTTP transport (lazy ``httpx`` with a stdlib
        ``urllib.request`` fallback — no hard dependency). ``transport`` is
        injected only by tests with a recorded fixture; production passes
        ``None``. Any failure degrades to :meth:`sample` (PRD Step 10).
        """
        from .http import http_get_json

        url = getattr(self.settings, "usgs_feed_url", None) or self.DEFAULT_URL
        try:
            return http_get_json(url, timeout=10.0, transport=transport)
        except Exception:
            log.exception("USGS fetch failed; using sample()")
            return self.sample()

    # ---------------------------------------------------------------- assess
    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        """Flag M4.5+ (PRD Step 1, Module B)."""
        breaches = [o for o in observations if o["magnitude"] >= USGS_MAGNITUDE_THRESHOLD]
        if not breaches:
            top = max((o["magnitude"] for o in observations), default=0.0)
            return False, Priority.INFO, [
                f"usgs: max M{top:.1f} below activation M{USGS_MAGNITUDE_THRESHOLD}"
            ]
        strongest = max(breaches, key=lambda o: o["magnitude"])
        prio = Priority.CRITICAL if strongest["magnitude"] >= 6.0 else Priority.HIGH
        reasoning = [
            f"usgs: {len(breaches)} quake(s) >= M{USGS_MAGNITUDE_THRESHOLD} (PRD Step 1, Module B)",
            f"strongest M{strongest['magnitude']:.1f} @ {strongest['place']}",
        ]
        if strongest.get("tsunami"):
            reasoning.append("USGS tsunami flag set")
        return True, prio, reasoning

    # ----------------------------------------------------------------- event
    def build_event(self, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        breaches = [o for o in observations if o["magnitude"] >= USGS_MAGNITUDE_THRESHOLD]
        if not breaches:
            return None
        strongest = max(breaches, key=lambda o: o["magnitude"])
        return _event_dict(
            incident_id=f"usgs:{strongest['id']}",
            lat=strongest["lat"],
            lon=strongest["lon"],
            magnitude=strongest["magnitude"],
            source="USGS",
            place=strongest.get("place", ""),
            detected_at=utcnow_iso(),
            extra={"depth_km": strongest.get("depth_km"), "tsunami": strongest.get("tsunami")},
        )


class NCSFeedAgent(BaseFeedAgent):
    """National Center for Seismology (India) RSS feed (PRD Step 2, Module B).

    NCS publishes India-region earthquakes as an RSS feed; each item title
    encodes magnitude and location. Same M4.5+ activation threshold applies.
    """

    feed_name = "ncs"
    module = Module.EARTHQUAKE

    # ----------------------------------------------------------------- parse
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        """Decode pre-parsed NCS RSS items into observation dicts.

        ``raw`` is a list of item dicts (``feedparser`` entries are coerced to
        this shape by :meth:`fetch`), keeping :meth:`parse` pure and testable.
        """
        items = raw if isinstance(raw, list) else (raw or {}).get("entries", [])
        out: list[dict[str, Any]] = []
        for item in items or []:
            mag = item.get("magnitude")
            if mag is None:
                mag = self._mag_from_title(item.get("title", ""))
            if mag is None:
                continue
            out.append(
                {
                    "id": item.get("id") or item.get("guid") or item.get("title", "ncs"),
                    "magnitude": float(mag),
                    "lat": float(item.get("lat", 0.0) or 0.0),
                    "lon": float(item.get("lon", 0.0) or 0.0),
                    "depth_km": float(item.get("depth_km", 0.0) or 0.0),
                    "place": item.get("region") or item.get("title", ""),
                    "published": item.get("published", ""),
                }
            )
        return out

    @staticmethod
    def _mag_from_title(title: str) -> float | None:
        """Best-effort magnitude extraction from an RSS title (stdlib only)."""
        import re

        m = re.search(r"M\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)", title or "", re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        return None

    # ---------------------------------------------------------------- sample
    def sample(self) -> list[dict[str, Any]]:
        """Offline NCS RSS fixture (one M5.2 in Uttarakhand, one M3.4)."""
        return [
            {
                "id": "ncs-2026-0608-01",
                "title": "M:5.2, Uttarkashi, Uttarakhand",
                "magnitude": 5.2,
                "lat": 30.73,
                "lon": 78.45,
                "depth_km": 12.0,
                "region": "Uttarkashi, Uttarakhand",
                "published": "2026-06-08T04:12:00+05:30",
            },
            {
                "id": "ncs-2026-0608-02",
                "title": "M:3.4, Andaman Islands",
                "magnitude": 3.4,
                "lat": 11.67,
                "lon": 92.74,
                "depth_km": 30.0,
                "region": "Andaman Islands",
                "published": "2026-06-08T03:55:00+05:30",
            },
        ]

    # ----------------------------------------------------------------- fetch
    def fetch(self, transport: Any = None) -> list[dict[str, Any]]:  # pragma: no cover - network path
        """Live GET + ``feedparser`` parse of the NCS RSS feed.

        The RSS body is fetched through the shared HTTP transport seam so the
        injected/recorded ``transport`` is honoured (tests stay offline and the
        live-resilient circuit breaker observes real upstream failures);
        ``feedparser`` then parses the in-memory text. Any failure degrades to
        ``sample()``.
        """
        from .http import http_get_text

        url = "https://riseq.seismo.gov.in/riseq/earthquake/rss"
        try:
            text = http_get_text(url, timeout=10.0, transport=transport)
            import feedparser  # type: ignore

            parsed = feedparser.parse(text)
            items: list[dict[str, Any]] = []
            for e in getattr(parsed, "entries", []) or []:
                items.append(
                    {
                        "id": getattr(e, "id", None) or getattr(e, "guid", None),
                        "title": getattr(e, "title", ""),
                        "published": getattr(e, "published", ""),
                    }
                )
            return items or self.sample()
        except Exception:
            log.exception("NCS fetch failed; using sample()")
            return self.sample()

    # ---------------------------------------------------------------- assess
    def assess(self, observations: list[dict[str, Any]]) -> tuple[bool, Priority, list[str]]:
        breaches = [o for o in observations if o["magnitude"] >= USGS_MAGNITUDE_THRESHOLD]
        if not breaches:
            top = max((o["magnitude"] for o in observations), default=0.0)
            return False, Priority.INFO, [
                f"ncs: max M{top:.1f} below activation M{USGS_MAGNITUDE_THRESHOLD}"
            ]
        strongest = max(breaches, key=lambda o: o["magnitude"])
        prio = Priority.CRITICAL if strongest["magnitude"] >= 6.0 else Priority.HIGH
        return True, prio, [
            f"ncs: {len(breaches)} India quake(s) >= M{USGS_MAGNITUDE_THRESHOLD} (PRD Step 1)",
            f"strongest M{strongest['magnitude']:.1f} @ {strongest['place']}",
        ]

    # ----------------------------------------------------------------- event
    def build_event(self, observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        breaches = [o for o in observations if o["magnitude"] >= USGS_MAGNITUDE_THRESHOLD]
        if not breaches:
            return None
        strongest = max(breaches, key=lambda o: o["magnitude"])
        return _event_dict(
            incident_id=f"ncs:{strongest['id']}",
            lat=strongest["lat"],
            lon=strongest["lon"],
            magnitude=strongest["magnitude"],
            source="NCS",
            place=strongest.get("place", ""),
            detected_at=utcnow_iso(),
            extra={"depth_km": strongest.get("depth_km")},
        )
