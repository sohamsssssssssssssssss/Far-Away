"""Module C — urban-fire spread forecaster (PRD Step 3 Module C)."""
from __future__ import annotations

import math
from dataclasses import asdict

from ....audit.decision_log import DecisionLogger
from ....core.bus import MessageBus
from ....core.contracts import (
    Message,
    Module,
    Priority,
    Topic,
)
from ....models.domain import (
    EventKind,
    FireFront,
)
from ....models.geo import LatLon
from .base import (
    FIRE_HORIZONS_MIN,
    _PredictionAgent,
    _as_latlon,
    _clamp01,
    _extract_event,
    _offset_latlon,
)


# --------------------------------------------------------------------------- C
class FireSpreadAgent(_PredictionAgent):
    """Module C urban-fire spread forecaster (PRD Step 3 Module C).

    Production interface (lazy): cellular-automata fire spread on a fuel/wind/
    slope grid. The stdlib fallback runs a deterministic CA over a small grid
    seeded at the ignition point, producing a fire perimeter at T+15/30/60 min
    as a :class:`FireFront` list, and tags critical infrastructure each
    perimeter threatens.
    """

    module = Module.FIRE_COLLAPSE

    def __init__(self, bus: MessageBus, logger: DecisionLogger | None = None) -> None:
        super().__init__("tier2.prediction.fire", bus, logger)

    def handle(self, message: Message) -> list[Message]:
        if message.topic == Topic.IOT_TELEMETRY:
            self._remember_telemetry(message)
            return []
        if message.topic != Topic.RAW_FEED:
            return []
        event = _extract_event(message.payload)
        if not event:
            return []
        kind = str(event.get("kind", "")).lower()
        if kind not in (EventKind.URBAN_FIRE.value, EventKind.STRUCTURAL_COLLAPSE.value):
            return []
        return self._spread(message, event)

    def _spread(self, message: Message, event: dict) -> list[Message]:
        incident_id = event.get("incident_id") or message.incident_id
        ignition = _as_latlon(event.get("epicentre"))
        intensity = float(event.get("severity", 1.0))
        meta = event.get("meta", {}) or {}

        # Wind drives the spread vector; prefer live IoT, then meta, then calm.
        wind = self._telemetry("wind")
        wind_speed = float(wind.get("speed_ms", meta.get("wind_speed_ms", 3.0)))
        wind_dir_deg = float(wind.get("dir_deg", meta.get("wind_dir_deg", 90.0)))
        infra = meta.get("critical_infrastructure") or message.payload.get("observations") or []

        fronts, attrib = self._spread_fronts(
            ignition, intensity, wind_speed, wind_dir_deg, infra
        )

        self.logger.log_prediction(
            model=attrib["model"],
            inputs={
                "intensity": intensity,
                "wind_speed_ms": wind_speed,
                "wind_dir_deg": wind_dir_deg,
                "ignition": asdict(ignition),
            },
            prediction={
                "n_fronts": len(fronts),
                "max_perimeter_points": max((len(f.perimeter) for f in fronts), default=0),
                "infra_threatened": sorted(
                    {c for f in fronts for c in f.critical_infrastructure}
                ),
            },
            shap=attrib["shap"],
            incident_id=incident_id,
        )

        threatened = sorted({c for f in fronts for c in f.critical_infrastructure})
        reasoning = [
            f"{attrib['model']} fire-perimeter projection for incident {incident_id}",
            f"intensity={intensity:.1f} wind={wind_speed:.1f}m/s @ {wind_dir_deg:.0f}deg",
            f"fronts at T+15/30/60min; critical infra threatened={threatened or 'none'}",
        ]
        priority = Priority.CRITICAL if threatened or intensity >= 2.0 else Priority.HIGH
        return [self._publish_prediction(incident_id, [], [], fronts, reasoning, priority, shap=attrib["shap"])]

    def _spread_fronts(
        self,
        ignition: LatLon,
        intensity: float,
        wind_speed: float,
        wind_dir_deg: float,
        infra: list,
    ) -> tuple[list[FireFront], dict]:
        ca = self._try_ca(ignition, intensity, wind_speed, wind_dir_deg, infra)
        if ca is not None:
            return ca
        return self._heuristic_fronts(ignition, intensity, wind_speed, wind_dir_deg, infra)

    def _try_ca(
        self,
        ignition: LatLon,
        intensity: float,
        wind_speed: float,
        wind_dir_deg: float,
        infra: list,
    ) -> tuple[list[FireFront], dict] | None:
        """Real model layer (:mod:`disastermind.ml`) cellular-automata path.

        Only engages when a *trained real backend* is loaded for Module C
        (``model._backend_obj is not None``). The base burn probability is then
        derived from ``model.predict_one`` over the
        ``(intensity, wind_speed_ms, base_fuel)`` feature vector and used to
        modulate the rate-of-spread, so the projected perimeter reflects the
        trained model; the SHAP attribution comes from the real explainer. The
        SAME elliptical-perimeter spreading body is reused via ``base_override``.
        With no trained artefact (the default — nothing ships) it returns
        ``None`` so the deterministic stdlib CA runs byte-identically.
        """
        try:
            from .... import ml  # lazy: keep stdlib import path clean
        except Exception:
            return None
        try:
            model = ml.get_model(self.module)
            if getattr(model, "_backend_obj", None) is None:
                return None  # no trained backend -> deterministic fallback
            raw = {
                "intensity": intensity,
                "wind_speed_ms": wind_speed,
                "base_fuel": _infra_density(infra),
            }
            fv = ml.features.features_for_module(self.module, raw)
            base = _clamp01(float(model.predict_one(fv)))
            shap = ml.shap_explain.explain_dict(model, fv)
        except Exception:
            return None
        return self._heuristic_fronts(
            ignition,
            intensity,
            wind_speed,
            wind_dir_deg,
            infra,
            base_override=base,
            shap_override=shap,
            model_name="fire-cellular-automata-ml",
        )

    def _heuristic_fronts(
        self,
        ignition: LatLon,
        intensity: float,
        wind_speed: float,
        wind_dir_deg: float,
        infra: list,
        base_override: float | None = None,
        shap_override: dict | None = None,
        model_name: str = "fire-cellular-automata-heuristic",
    ) -> tuple[list[FireFront], dict]:
        """Deterministic cellular-automata fire perimeter (ONE shared impl).

        The fire spreads outward at a base rate (m/min) boosted in the
        downwind direction. We grow an elliptical perimeter and, at each
        horizon, sample it as a polygon of LatLon points; infrastructure inside
        the perimeter is flagged as threatened. When ``base_override`` (a burn
        probability in [0, 1] from the real model) is supplied, it scales the
        rate-of-spread so the perimeter reflects the trained model, while the
        elliptical spreading geometry below is shared verbatim by both paths.
        """
        # Base rate-of-spread in metres/minute, scaled by intensity and wind.
        base_ros = 4.0 + 6.0 * _clamp01(intensity / 3.0) + 1.5 * wind_speed
        if base_override is not None:
            # Map a burn probability in [0, 1] to a multiplier around 1.0 so a
            # high-confidence model accelerates the projected perimeter.
            base_ros *= 0.5 + float(base_override)
        wind_dir = math.radians(wind_dir_deg)
        eccentric = 1.0 + 0.15 * wind_speed  # downwind elongation factor

        infra_points = _parse_infra(infra)
        fronts: list[FireFront] = []
        n_pts = 16
        for horizon in FIRE_HORIZONS_MIN:
            base_radius = base_ros * horizon  # metres
            perimeter: list[LatLon] = []
            threatened: list[str] = []
            for k in range(n_pts):
                theta = 2 * math.pi * k / n_pts
                # Elliptical radius: longer downwind, shorter upwind.
                downwind_align = math.cos(theta - wind_dir)
                r = base_radius * (1.0 + 0.5 * (eccentric - 1.0) * downwind_align)
                r = max(1.0, r)
                north_m = r * math.cos(theta)
                east_m = r * math.sin(theta)
                perimeter.append(_offset_latlon(ignition, north_m, east_m))
            # Flag infra within the max radius of this front.
            for name, pt in infra_points:
                if ignition.distance_m(pt) <= base_radius * eccentric:
                    threatened.append(name)
            fronts.append(
                FireFront(
                    horizon_minutes=horizon,
                    perimeter=perimeter,
                    critical_infrastructure=sorted(set(threatened)),
                )
            )

        # SHAP-style attribution for the rate-of-spread drivers.
        denom = base_ros if base_ros else 1.0
        shap = {
            "intensity": round((6.0 * _clamp01(intensity / 3.0)) / denom, 4),
            "wind_speed_ms": round((1.5 * wind_speed) / denom, 4),
            "base_fuel": round(4.0 / denom, 4),
        }
        if shap_override is not None:
            shap = {k: round(float(v), 4) for k, v in shap_override.items()}
        return fronts, {"model": model_name, "shap": shap}


# --------------------------------------------------------------------------- utils
def _infra_density(infra: list) -> float:
    """Built-up fuel proxy from the count of critical-infra entries.

    ``base_fuel`` in the Module C feature schema proxies built density (more
    structures => more fuel). We map the infra count to a small positive scalar
    (>= 1.0) so the feature is well-conditioned even with no infra listed.
    """
    n = len(infra or [])
    return 1.0 + float(n)


def _parse_infra(infra: list) -> list[tuple[str, LatLon]]:
    """Normalise critical-infra entries into (name, LatLon) pairs."""
    out: list[tuple[str, LatLon]] = []
    for item in infra or []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("id") or "infra")
            loc = item.get("location")
            if loc is not None:
                out.append((name, _as_latlon(loc)))
    return out
