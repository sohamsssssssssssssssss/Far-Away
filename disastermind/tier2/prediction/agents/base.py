"""Shared Tier 2 prediction plumbing (base class + cross-agent helpers).

Houses the :class:`_PredictionAgent` base every hazard specialist extends, plus
the small deterministic helpers (``_clamp01``/``_logistic``/``_as_latlon``/
``_extract_event``/``_shap_features``/``_offset_latlon``) and forecast-horizon
constants reused across the cyclone, earthquake and fire modules.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

from ....audit.decision_log import DecisionLogger
from ....core.agent import BaseAgent
from ....core.bus import MessageBus
from ....core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
)
from ....models.domain import (
    BuildingImpact,
    FireFront,
    RiskCell,
)
from ....models.geo import LatLon

# Forecast horizons (PRD Step 3).
FLOOD_HORIZONS_MIN = (360, 720, 1440, 2880)  # T+6/12/24/48 h
FIRE_HORIZONS_MIN = (15, 30, 60)  # T+15/30/60 min

# Construction-class HAZUS-style fragility coefficients (Module B). Higher beta
# => higher collapse probability for a given shaking intensity. Indicative
# values tuned so kutcha << pucca << RCC in resilience.
FRAGILITY = {
    "kutcha": {"mmi_threshold": 5.0, "slope": 0.55},
    "pucca": {"mmi_threshold": 6.5, "slope": 0.40},
    "rcc": {"mmi_threshold": 7.5, "slope": 0.28},
    "unknown": {"mmi_threshold": 6.0, "slope": 0.45},
}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _logistic(x: float) -> float:
    """Numerically-safe logistic squash to (0, 1)."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _as_latlon(obj: Any, default: LatLon | None = None) -> LatLon:
    """Coerce a dict / LatLon / [lat, lon] into a :class:`LatLon`."""
    if isinstance(obj, LatLon):
        return obj
    if isinstance(obj, dict) and "lat" in obj and "lon" in obj:
        return LatLon(float(obj["lat"]), float(obj["lon"]))
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        return LatLon(float(obj[0]), float(obj[1]))
    return default or LatLon(0.0, 0.0)


def _extract_event(payload: dict) -> dict | None:
    """Pull the embedded DisasterEvent dict from a RAW_FEED payload, if any."""
    ev = payload.get("event")
    return ev if isinstance(ev, dict) else None


def _shap_features(shap: dict[str, float] | None) -> list[dict]:
    """Convert a ``{feature: signed_value}`` SHAP dict into the dashboard wire
    shape ``[{feature, value, direction}]`` (most-influential first) — PRD Step 9
    explainability surfaced on the WebSocket payload, not just the audit log."""
    out: list[dict] = []
    for feat, val in (shap or {}).items():
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        out.append(
            {"feature": str(feat), "value": round(v, 4), "direction": "up" if v >= 0 else "down"}
        )
    out.sort(key=lambda d: abs(d["value"]), reverse=True)
    return out


def _offset_latlon(origin: LatLon, north_m: float, east_m: float) -> LatLon:
    """Translate a point by (north, east) metres (equirectangular approx.)."""
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(origin.lat)) or 1e-6
    return LatLon(
        origin.lat + north_m / m_per_deg_lat,
        origin.lon + east_m / m_per_deg_lon,
    )


class _PredictionAgent(BaseAgent):
    """Shared Tier 2 prediction plumbing.

    Tier 2 SPECIALIST agents *do* hold decision authority within their domain
    (PRD Step 2); only Tier 3 sets ``decision_authority = False``. We subscribe
    to RAW_FEED and IOT_TELEMETRY and remember the last telemetry snapshot so a
    prediction can fuse live sensor readings with feed-driven events.
    """

    tier = Tier.SPECIALIST
    module: Module = Module.ALL

    def __init__(self, name: str, bus: MessageBus, logger: DecisionLogger | None = None) -> None:
        super().__init__(
            name=name,
            bus=bus,
            logger=logger,
            subscriptions=[Topic.RAW_FEED, Topic.IOT_TELEMETRY],
        )
        self._last_telemetry: dict[str, dict] = {}

    # --- helpers ---------------------------------------------------------
    def _remember_telemetry(self, message: Message) -> None:
        kind = str(message.payload.get("kind", "sensor"))
        self._last_telemetry[kind] = message.payload

    def _telemetry(self, kind: str) -> dict:
        return self._last_telemetry.get(kind, {})

    def _publish_prediction(
        self,
        incident_id: str | None,
        risk_cells: list[RiskCell],
        buildings: list[BuildingImpact],
        fire_fronts: list[FireFront],
        reasoning: list[str],
        priority: Priority,
        shap: dict[str, float] | None = None,
    ) -> Message:
        payload = {
            "kind": "risk",
            "shap_features": _shap_features(shap),
            "incident_id": incident_id,
            "module": self.module.value,
            "risk_cells": [asdict(c) for c in risk_cells],
            "buildings": [asdict(b) for b in buildings],
            "fire_fronts": [asdict(f) for f in fire_fronts],
        }
        return Message(
            sender=self.name,
            recipient="tier2.cascade",
            type=MessageType.ALERT,
            priority=priority,
            payload=payload,
            reasoning=reasoning,
            topic=Topic.PREDICTION,
            incident_id=incident_id,
            module=self.module,
        )
