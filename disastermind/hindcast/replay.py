"""Replay Fani through the pipeline using only pre-cutoff data; score honestly.

Methodology (leak-free):
  * Pick a forecast cutoff = ``lead_hours`` before the REAL landfall time.
  * Use ONLY best-track points at/before the cutoff.
  * Extrapolate landfall with a TRANSPARENT great-circle persistence forecast
    (recent motion projected forward). This is a deliberately simple baseline —
    in production IMD's dynamical forecast is the input; we measure how far even
    a naive extrapolation lands from the real coast, as a floor on usefulness.
  * Drive the real DisasterMind activation (orchestration.triggers) and pipeline
    (build_system) with the pre-cutoff cyclone state, and record whether it
    activates and produces an evacuation/dispatch plan with this much lead time.
  * Score against the DOCUMENTED outcome (real landfall point/time/intensity).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models.geo import LatLon, haversine
from .fani import FaniCase, TrackPoint

_FMT = "%Y-%m-%d %H:%M:%S"


def _parse(t: str) -> datetime:
    return datetime.strptime(t, _FMT)


def _hours(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 3600.0


def extrapolate_landfall(
    points: list[TrackPoint], target_time: str
) -> tuple[float, float]:
    """Great-circle persistence extrapolation to ``target_time`` (lat, lon).

    Uses the mean velocity of the last few pre-cutoff points (deg/hr) projected
    forward. Transparent and naive on purpose — a floor, not IMD's model.
    """
    pts = points[-4:] if len(points) >= 4 else points[:]
    if len(pts) < 2:
        p = pts[-1]
        return p.lat, p.lon
    t0, t1 = _parse(pts[0].time), _parse(pts[-1].time)
    span = _hours(t1, t0) or 1.0
    dlat = (pts[-1].lat - pts[0].lat) / span
    dlon = (pts[-1].lon - pts[0].lon) / span
    fwd = _hours(_parse(target_time), t1)
    return pts[-1].lat + dlat * fwd, pts[-1].lon + dlon * fwd


@dataclass
class HindcastResult:
    storm: str
    lead_hours: float
    cutoff_time: str
    actual_landfall_time: str
    actual_landfall: tuple[float, float]
    predicted_landfall: tuple[float, float]
    track_error_km: float
    cutoff_intensity_kt: float | None
    activated: bool
    activation_basis: str
    produced_plan: bool
    dispatches: int
    routes: int
    notes: list[str] = field(default_factory=list)


def run_hindcast(case: FaniCase, lead_hours: float = 48.0) -> HindcastResult:
    """Replay the case at a forecast cutoff ``lead_hours`` before real landfall."""
    landfall = case.landfall_point()
    lf_time = _parse(landfall.time)
    cutoff_dt = lf_time - timedelta(hours=lead_hours)
    cutoff_iso = cutoff_dt.strftime(_FMT)
    before = case.points_before(cutoff_iso)

    notes: list[str] = []
    if not before:
        notes.append("no track data before cutoff — storm had not formed yet")
        return HindcastResult(
            storm=case.storm, lead_hours=lead_hours, cutoff_time=cutoff_iso,
            actual_landfall_time=landfall.time,
            actual_landfall=(landfall.lat, landfall.lon),
            predicted_landfall=(0.0, 0.0), track_error_km=float("nan"),
            cutoff_intensity_kt=None, activated=False,
            activation_basis="storm not yet formed", produced_plan=False,
            dispatches=0, routes=0, notes=notes,
        )

    pred_lat, pred_lon = extrapolate_landfall(before, landfall.time)
    err_km = haversine(LatLon(pred_lat, pred_lon), LatLon(landfall.lat, landfall.lon)) / 1000.0
    last = before[-1]
    cutoff_wind = last.wind_kt

    # --- real activation logic (PRD Step 1, Module A) --------------------------
    # At the cutoff the storm is a named, intensifying system tracking toward the
    # coast; an IMD cyclonic-storm alert (wind >= 34 kt) would be in force.
    from ..orchestration.triggers import Signals, should_activate

    is_cyclone_alert = bool(cutoff_wind and cutoff_wind >= 34.0)
    module = should_activate(Signals(imd_cyclone_alert=is_cyclone_alert))
    activated = module is not None
    basis = (
        f"IMD cyclonic-storm alert in force (wind {cutoff_wind:.0f} kt) "
        f"{lead_hours:.0f} h before landfall"
        if activated
        else "no activation trigger met at cutoff"
    )

    # --- drive the real coordination pipeline at the predicted landfall --------
    dispatches = routes = 0
    produced = False
    try:
        from ..audit.decision_log import DecisionLogger
        from ..core.bus import InMemoryBus
        from ..core.config import Settings
        from ..core.contracts import (
            Message,
            MessageType,
            Module,
            Priority,
            Topic,
        )
        from ..orchestration.build import build_system
        from ..scenarios.base import seed_field_teams

        bus = InMemoryBus()
        loop = build_system(bus=bus, logger=DecisionLogger.null(), settings=Settings())
        seed_field_teams(bus)
        # Inject a cyclone/flood event at the PREDICTED landfall (what the system
        # would have known at the cutoff), then run the pipeline.
        bus.publish(
            Message(
                sender="ingest.imd", recipient="tier2.prediction",
                type=MessageType.ALERT, priority=Priority.CRITICAL,
                topic=Topic.RAW_FEED, incident_id=f"hindcast:{case.storm.lower()}-{case.season}",
                module=Module.CYCLONE_FLOOD,
                payload={
                    "kind": "imd_cyclone",
                    "event": {
                        "kind": "cyclone",
                        "incident_id": f"hindcast:{case.storm.lower()}-{case.season}",
                        "epicentre": {"lat": pred_lat, "lon": pred_lon},
                        "severity": 4.0,
                        "meta": {"rainfall_mm": 220.0, "storm_surge_m": 1.5,
                                 "place": case.outcome.get("landfall_place", "coast")},
                    },
                    "observations": [{"population": 1200}],
                },
            )
        )
        for _ in range(3):
            loop.run_once()
        dispatches = sum(
            1 for m in bus.history
            if m.topic == Topic.DISPATCH and (m.payload or {}).get("kind") != "dispatch_ack"
        )
        routes = sum(1 for m in bus.history if m.topic == Topic.ROUTING_PLAN)
        produced = dispatches > 0 or routes > 0
    except Exception as exc:  # pragma: no cover - pipeline must not break the hindcast
        notes.append(f"pipeline replay degraded: {type(exc).__name__}")

    notes.append(
        f"At {lead_hours:.0f} h lead, the real evacuation ({case.outcome.get('evacuated')}) "
        f"was underway; the documented toll was {case.outcome.get('deaths')} deaths despite a "
        f"{case.outcome.get('landfall_intensity')} landfall."
    )
    return HindcastResult(
        storm=case.storm, lead_hours=lead_hours, cutoff_time=cutoff_iso,
        actual_landfall_time=landfall.time,
        actual_landfall=(landfall.lat, landfall.lon),
        predicted_landfall=(round(pred_lat, 3), round(pred_lon, 3)),
        track_error_km=round(err_km, 1), cutoff_intensity_kt=cutoff_wind,
        activated=activated, activation_basis=basis,
        produced_plan=produced, dispatches=dispatches, routes=routes, notes=notes,
    )
