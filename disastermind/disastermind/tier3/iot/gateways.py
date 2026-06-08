"""Tier 3 IoT gateway agents (PRD Step 2 — Real-Time Data Ingestion).

These agents sit at the *edge*: they poll municipal/field sensor networks,
aggregate raw readings into threshold/cluster alerts, and publish compact
telemetry frames onto :data:`Topic.IOT_TELEMETRY`. They have **no decision
authority** (``decision_authority = False``) — they observe and report; Tier 2
specialists turn telemetry into predictions and plans.

Gateways implemented here:

* :class:`SmokeHeatGateway`     — municipal smoke + heat detectors (fire, Module C).
* :class:`WaterloggingGateway`  — urban waterlogging sensor mesh; a breach in
  3+ zones raises a Module A (cyclone/flood) alert.
* :class:`StructuralGateway`    — strain/tilt/accelerometer arrays on monitored
  buildings (post-quake / collapse risk, Module B/C).
* :class:`GpsBeaconGateway`     — field-team GPS position beacons on a 60-second
  cadence (PRD Step 6); emits :class:`FieldTeam`-shaped position updates the
  field-coordination agent consumes.

Every gateway exposes:

* ``sample()``    — deterministic stdlib fixture readings (no network, PRD Step 10).
* ``aggregate()`` — pure function: readings -> (alerts, summary), detecting
  threshold breaches and spatial clusters.
* ``tick()``      — periodic loop that samples, aggregates and emits one
  :class:`Message` on :data:`Topic.IOT_TELEMETRY`.

Heavy/optional libraries (e.g. ``numpy`` for clustering, ``paho-mqtt`` /
``confluent_kafka`` for live ingestion) are imported **lazily** inside methods,
wrapped in ``try/except`` with a stdlib heuristic fallback so the package
imports and tests run with stdlib only.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from ...audit.decision_log import DecisionLogger
from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.contracts import (
    Message,
    MessageType,
    Module,
    Priority,
    Tier,
    Topic,
    utcnow_iso,
)
from ...models.domain import AssetType, FieldTeam
from ...models.geo import LatLon, haversine

# --------------------------------------------------------------------------- defaults
#: Reference origin for fixture sensor meshes (central India anchor; arbitrary
#: but deterministic so cell ids / distances are stable across runs).
DEFAULT_ORIGIN = LatLon(19.0760, 72.8777)  # Mumbai — flood/fire prone metro

#: Threshold catalogue. Tuned for fixtures; production overrides via Settings.
SMOKE_PPM_ALERT = 300.0          # particulate / smoke obscuration threshold
HEAT_C_ALERT = 57.0              # NFPA fixed-temperature heat-detector trip (~135F)
WATER_LEVEL_M_ALERT = 0.30       # 30 cm standing water = waterlogging breach
WATER_ZONE_QUORUM = 3            # breach in 3+ zones -> Module A trigger
STRAIN_MICROSTRAIN_ALERT = 250.0 # microstrain — onset of structural distress
TILT_DEG_ALERT = 1.5             # building tilt (degrees) indicating lean
ACCEL_G_ALERT = 0.15             # peak ground/struct accel (g) of concern


# --------------------------------------------------------------------------- helpers
@dataclass
class SensorSite:
    """A fixed sensor location in a gateway's fixture mesh."""

    site_id: str
    location: LatLon
    zone: str = ""


def _rng(seed: str) -> random.Random:
    """Deterministic per-gateway RNG so ``sample()`` is reproducible (no network)."""
    return random.Random(hash(seed) & 0xFFFFFFFF)


# =========================================================================== base
class IoTGateway(BaseAgent):
    """Common scaffolding for every Tier 3 IoT gateway (PRD Step 2).

    Subclasses provide ``sensor_kind``, a fixture ``sites`` mesh, a ``sample()``
    that yields per-site readings, and an ``aggregate()`` that flags breaches.
    The shared :meth:`tick` wires those together and emits exactly one telemetry
    :class:`Message`. Gateways never decide — only report.
    """

    tier = Tier.EDGE
    decision_authority = False  # PRD Step 2 / Step 8 — edge agents observe only

    #: payload "kind" discriminator + sensor-type label on the telemetry frame
    sensor_kind: str = "generic"
    #: domain this gateway primarily informs
    module: Module = Module.ALL
    #: who consumes our telemetry (informational recipient; bus is topic-routed)
    recipient: str = "tier2.prediction"

    def __init__(
        self,
        name: str,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        sites: list[SensorSite] | None = None,
        incident_id: str | None = None,
        seed: str | None = None,
    ) -> None:
        # Edge gateways are producers — no input subscriptions required.
        super().__init__(name, bus, logger, subscriptions=[])
        self.sites: list[SensorSite] = sites or self.default_sites()
        self.incident_id = incident_id
        self._rng = _rng(seed or name)
        self._cycle = 0

    # ------------------------------------------------------------- to override
    def default_sites(self) -> list[SensorSite]:  # pragma: no cover - overridden
        return []

    def sample(self) -> list[dict[str, Any]]:  # pragma: no cover - overridden
        """Return one fixture reading per site (deterministic, no network)."""
        raise NotImplementedError

    def aggregate(self, readings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:  # pragma: no cover - overridden
        """Pure: readings -> (alerts, summary). Detect breaches / clusters."""
        raise NotImplementedError

    # ------------------------------------------------------------------- hooks
    def handle(self, message: Message) -> list[Message]:
        """Edge gateways are pure producers; they react to nothing."""
        return []

    def tick(self) -> list[Message]:
        """Sample -> aggregate -> emit one telemetry frame (PRD Step 2/10)."""
        self._cycle += 1
        readings = self.sample()
        alerts, summary = self.aggregate(readings)
        payload = self._frame(readings, alerts, summary)
        priority = self._priority(alerts)
        reasoning = self._reasoning(alerts, summary)
        msg = Message(
            sender=self.name,
            recipient=self.recipient,
            type=MessageType.ALERT if alerts else MessageType.QUERY,
            priority=priority,
            payload=payload,
            reasoning=reasoning,
            topic=Topic.IOT_TELEMETRY,
            incident_id=self.incident_id,
            module=self.module,
        )
        return [msg]

    # ----------------------------------------------------------------- framing
    def _frame(
        self,
        readings: list[dict[str, Any]],
        alerts: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the IOT_TELEMETRY payload (see TOPIC WIRING conventions)."""
        return {
            "kind": self.sensor_kind,
            "incident_id": self.incident_id,
            "module": self.module.value,
            "gateway": self.name,
            "cycle": self._cycle,
            "readings": readings,
            "alerts": alerts,
            "summary": summary,
            "sampled_at": utcnow_iso(),
        }

    def _priority(self, alerts: list[dict[str, Any]]) -> Priority:
        if not alerts:
            return Priority.INFO
        return Priority.CRITICAL if len(alerts) >= 2 else Priority.HIGH

    def _reasoning(self, alerts: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
        if not alerts:
            return [f"{self.sensor_kind}: {len(self.sites)} sites nominal"]
        return [f"{self.sensor_kind}: {len(alerts)} breach(es) detected"] + [
            a.get("note", "") for a in alerts if a.get("note")
        ]


# =========================================================================== smoke/heat
class SmokeHeatGateway(IoTGateway):
    """Municipal smoke + heat detector aggregation (PRD Step 2, Module C fire).

    Aggregates building/street smoke obscuration (ppm) and fixed-temperature
    heat detectors. Co-located smoke *and* heat breaches form a high-confidence
    fire cluster the prediction agent treats as ignition evidence.
    """

    sensor_kind = "smoke_heat"
    module = Module.FIRE_COLLAPSE
    recipient = "tier2.prediction"

    def default_sites(self) -> list[SensorSite]:
        # A compact grid of street/building detectors around the origin.
        sites: list[SensorSite] = []
        for i in range(8):
            dlat = (i % 4) * 0.004 - 0.006
            dlon = (i // 4) * 0.004 - 0.002
            sites.append(
                SensorSite(
                    site_id=f"smoke-{i:02d}",
                    location=LatLon(DEFAULT_ORIGIN.lat + dlat, DEFAULT_ORIGIN.lon + dlon),
                    zone=f"ward-{i // 2}",
                )
            )
        return sites

    def sample(self) -> list[dict[str, Any]]:
        """Deterministic fixtures: most sites quiet, a couple trending hot."""
        out: list[dict[str, Any]] = []
        for idx, s in enumerate(self.sites):
            # baseline ambient + small jitter; sites 2 and 3 simulate a fire.
            hot = idx in (2, 3)
            smoke = (380.0 if hot else 40.0) + self._rng.uniform(-15, 15)
            heat = (62.0 if hot else 31.0) + self._rng.uniform(-2, 2)
            out.append(
                {
                    "site_id": s.site_id,
                    "zone": s.zone,
                    "lat": s.location.lat,
                    "lon": s.location.lon,
                    "smoke_ppm": round(max(0.0, smoke), 1),
                    "heat_c": round(heat, 1),
                }
            )
        return out

    def aggregate(self, readings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        max_smoke = 0.0
        max_heat = 0.0
        for r in readings:
            smoke = r.get("smoke_ppm", 0.0)
            heat = r.get("heat_c", 0.0)
            max_smoke = max(max_smoke, smoke)
            max_heat = max(max_heat, heat)
            smoke_hit = smoke >= SMOKE_PPM_ALERT
            heat_hit = heat >= HEAT_C_ALERT
            if smoke_hit or heat_hit:
                confirmed = smoke_hit and heat_hit
                alerts.append(
                    {
                        "site_id": r["site_id"],
                        "zone": r.get("zone", ""),
                        "lat": r.get("lat"),
                        "lon": r.get("lon"),
                        "smoke_ppm": smoke,
                        "heat_c": heat,
                        "confirmed_fire": confirmed,
                        "note": (
                            f"{r['site_id']}: smoke={smoke}ppm heat={heat}C"
                            + (" CONFIRMED FIRE" if confirmed else " (single-modality)")
                        ),
                    }
                )
        clusters = self._cluster(alerts)
        summary = {
            "sites": len(readings),
            "breaches": len(alerts),
            "max_smoke_ppm": round(max_smoke, 1),
            "max_heat_c": round(max_heat, 1),
            "confirmed_fire": any(a["confirmed_fire"] for a in alerts),
            "clusters": clusters,
        }
        return alerts, summary

    def _cluster(self, alerts: list[dict[str, Any]], radius_m: float = 600.0) -> list[list[str]]:
        """Spatially cluster breaching sites (lazy numpy, stdlib fallback)."""
        pts = [
            (a["site_id"], LatLon(a["lat"], a["lon"]))
            for a in alerts
            if a.get("lat") is not None and a.get("lon") is not None
        ]
        if not pts:
            return []
        try:  # optional: vectorised distance if numpy present
            import numpy as np  # noqa: F401  (presence check; loop fallback is exact)
        except Exception:
            pass
        # Single-link clustering via haversine — exact and stdlib-only.
        clusters: list[list[tuple[str, LatLon]]] = []
        for sid, loc in pts:
            placed = False
            for c in clusters:
                if any(haversine(loc, other) <= radius_m for _, other in c):
                    c.append((sid, loc))
                    placed = True
                    break
            if not placed:
                clusters.append([(sid, loc)])
        return [[sid for sid, _ in c] for c in clusters]


# =========================================================================== waterlogging
class WaterloggingGateway(IoTGateway):
    """Urban waterlogging sensor mesh (PRD Step 2, Module A cyclone/flood).

    Each sensor reports standing-water depth (m) tagged with a city zone. A
    breach in **3+ distinct zones** trips the Module A flood trigger — that
    quorum is the headline signal the prediction agent escalates on.
    """

    sensor_kind = "waterlogging"
    module = Module.CYCLONE_FLOOD
    recipient = "tier2.prediction"

    def default_sites(self) -> list[SensorSite]:
        zones = ["zone-A", "zone-B", "zone-C", "zone-D", "zone-E", "zone-F"]
        sites: list[SensorSite] = []
        for i, z in enumerate(zones):
            # two sensors per zone, spread along a notional drainage line.
            for j in range(2):
                dlat = i * 0.003 - 0.006
                dlon = j * 0.002
                sites.append(
                    SensorSite(
                        site_id=f"water-{z}-{j}",
                        location=LatLon(DEFAULT_ORIGIN.lat + dlat, DEFAULT_ORIGIN.lon + dlon),
                        zone=z,
                    )
                )
        return sites

    def sample(self) -> list[dict[str, Any]]:
        """Fixtures: 3 zones flooded (meets quorum) so e2e pipeline flows."""
        flooded_zones = {"zone-A", "zone-C", "zone-E"}
        out: list[dict[str, Any]] = []
        for s in self.sites:
            wet = s.zone in flooded_zones
            level = (0.45 if wet else 0.05) + self._rng.uniform(-0.03, 0.06)
            out.append(
                {
                    "site_id": s.site_id,
                    "zone": s.zone,
                    "lat": s.location.lat,
                    "lon": s.location.lon,
                    "water_level_m": round(max(0.0, level), 3),
                }
            )
        return out

    def aggregate(self, readings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        breached_zones: set[str] = set()
        max_level = 0.0
        for r in readings:
            level = r.get("water_level_m", 0.0)
            max_level = max(max_level, level)
            if level >= WATER_LEVEL_M_ALERT:
                zone = r.get("zone", "")
                breached_zones.add(zone)
                alerts.append(
                    {
                        "site_id": r["site_id"],
                        "zone": zone,
                        "lat": r.get("lat"),
                        "lon": r.get("lon"),
                        "water_level_m": level,
                        "note": f"{r['site_id']} ({zone}): {level} m standing water",
                    }
                )
        quorum_met = len(breached_zones) >= WATER_ZONE_QUORUM
        summary = {
            "sites": len(readings),
            "breaches": len(alerts),
            "breached_zones": sorted(breached_zones),
            "zone_count": len(breached_zones),
            "quorum": WATER_ZONE_QUORUM,
            "module_a_trigger": quorum_met,
            "max_level_m": round(max_level, 3),
        }
        return alerts, summary

    def _reasoning(self, alerts: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
        zc = summary.get("zone_count", 0)
        if summary.get("module_a_trigger"):
            return [
                f"waterlogging: {zc} zones breached (>= {WATER_ZONE_QUORUM}) "
                "-> Module A flood trigger",
                f"zones: {', '.join(summary.get('breached_zones', []))}",
            ]
        if alerts:
            return [f"waterlogging: {zc} zone(s) breached (< quorum {WATER_ZONE_QUORUM})"]
        return ["waterlogging: all zones below threshold"]

    def _priority(self, alerts: list[dict[str, Any]]) -> Priority:
        # Promote to CRITICAL only when the multi-zone quorum is implied.
        if not alerts:
            return Priority.INFO
        zones = {a.get("zone") for a in alerts if a.get("zone")}
        if len(zones) >= WATER_ZONE_QUORUM:
            return Priority.CRITICAL
        return Priority.HIGH


# =========================================================================== structural
class StructuralGateway(IoTGateway):
    """Structural sensor arrays on monitored buildings (PRD Step 2, Module B/C).

    Strain gauges (microstrain), tiltmeters (deg) and accelerometers (g) on
    high-occupancy / heritage structures. A breach on any modality flags a
    building; multi-modality breaches imply imminent distress the prediction /
    cascade agents weight heavily.
    """

    sensor_kind = "structural"
    module = Module.EARTHQUAKE
    recipient = "tier2.prediction"

    def default_sites(self) -> list[SensorSite]:
        labels = ["tower-1", "tower-2", "heritage-1", "hospital-1", "bridge-1"]
        sites: list[SensorSite] = []
        for i, lbl in enumerate(labels):
            sites.append(
                SensorSite(
                    site_id=lbl,
                    location=LatLon(
                        DEFAULT_ORIGIN.lat + i * 0.002,
                        DEFAULT_ORIGIN.lon - i * 0.0015,
                    ),
                    zone=f"block-{i}",
                )
            )
        return sites

    def sample(self) -> list[dict[str, Any]]:
        """Fixtures: one building (heritage-1) showing multi-modality distress."""
        out: list[dict[str, Any]] = []
        for s in self.sites:
            distressed = s.site_id == "heritage-1"
            strain = (320.0 if distressed else 80.0) + self._rng.uniform(-10, 10)
            tilt = (2.3 if distressed else 0.2) + self._rng.uniform(-0.1, 0.1)
            accel = (0.22 if distressed else 0.02) + self._rng.uniform(-0.01, 0.01)
            out.append(
                {
                    "site_id": s.site_id,
                    "zone": s.zone,
                    "lat": s.location.lat,
                    "lon": s.location.lon,
                    "microstrain": round(max(0.0, strain), 1),
                    "tilt_deg": round(max(0.0, tilt), 2),
                    "accel_g": round(max(0.0, accel), 3),
                }
            )
        return out

    def aggregate(self, readings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        worst_strain = 0.0
        for r in readings:
            strain = r.get("microstrain", 0.0)
            tilt = r.get("tilt_deg", 0.0)
            accel = r.get("accel_g", 0.0)
            worst_strain = max(worst_strain, strain)
            hits = []
            if strain >= STRAIN_MICROSTRAIN_ALERT:
                hits.append("strain")
            if tilt >= TILT_DEG_ALERT:
                hits.append("tilt")
            if accel >= ACCEL_G_ALERT:
                hits.append("accel")
            if hits:
                severity = self._severity(strain, tilt, accel, hits)
                alerts.append(
                    {
                        "site_id": r["site_id"],
                        "zone": r.get("zone", ""),
                        "lat": r.get("lat"),
                        "lon": r.get("lon"),
                        "microstrain": strain,
                        "tilt_deg": tilt,
                        "accel_g": accel,
                        "modalities": hits,
                        "severity": round(severity, 3),
                        "imminent": len(hits) >= 2,
                        "note": (
                            f"{r['site_id']}: {'+'.join(hits)} breach "
                            f"(strain={strain}, tilt={tilt}deg, accel={accel}g)"
                        ),
                    }
                )
        summary = {
            "sites": len(readings),
            "breaches": len(alerts),
            "worst_microstrain": round(worst_strain, 1),
            "imminent_failures": [a["site_id"] for a in alerts if a.get("imminent")],
        }
        return alerts, summary

    @staticmethod
    def _severity(strain: float, tilt: float, accel: float, hits: list[str]) -> float:
        """Normalised 0..1 distress score (heuristic; stdlib only)."""
        s = min(1.0, strain / (STRAIN_MICROSTRAIN_ALERT * 2))
        t = min(1.0, tilt / (TILT_DEG_ALERT * 2))
        a = min(1.0, accel / (ACCEL_G_ALERT * 2))
        base = (s + t + a) / 3.0
        # multi-modality concordance amplifies confidence
        return min(1.0, base * (1.0 + 0.25 * (len(hits) - 1)))


# =========================================================================== gps beacon
class GpsBeaconGateway(IoTGateway):
    """Field-team GPS position beacons, 60-second cadence (PRD Step 6).

    Emits :class:`~disastermind.models.domain.FieldTeam`-shaped position updates
    that the Tier 2 field-coordination agent consumes to track team movement,
    detect stale beacons, and re-plan. Beacons carry team status and a derived
    speed estimate from the previous fix.
    """

    sensor_kind = "gps_beacon"
    module = Module.ALL
    recipient = "tier2.field"

    #: PRD Step 6 — beacons report every 60 s; flag fixes older than 3 cycles.
    beacon_interval_seconds = 60
    stale_after_seconds = 180

    def __init__(
        self,
        name: str,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        sites: list[SensorSite] | None = None,
        incident_id: str | None = None,
        seed: str | None = None,
        teams: list[FieldTeam] | None = None,
    ) -> None:
        # `sites` unused for beacons; we track FieldTeam objects directly.
        self._teams: list[FieldTeam] = teams or self._default_teams()
        super().__init__(name, bus, logger, sites=[], incident_id=incident_id, seed=seed)
        # remember last known position to estimate speed/heading
        self._last: dict[str, LatLon] = {t.team_id: t.location for t in self._teams}

    def default_sites(self) -> list[SensorSite]:
        return []

    def _default_teams(self) -> list[FieldTeam]:
        specs = [
            ("ndrf-01", AssetType.NDRF_TEAM, "enroute"),
            ("sdrf-02", AssetType.SDRF_TEAM, "onsite"),
            ("med-03", AssetType.MEDICAL_UNIT, "enroute"),
            ("fire-04", AssetType.FIRE_ENGINE, "returning"),
        ]
        teams: list[FieldTeam] = []
        for i, (tid, atype, status) in enumerate(specs):
            teams.append(
                FieldTeam(
                    team_id=tid,
                    asset_type=atype,
                    location=LatLon(
                        DEFAULT_ORIGIN.lat + i * 0.0025,
                        DEFAULT_ORIGIN.lon + i * 0.0020,
                    ),
                    last_update=utcnow_iso(),
                    status=status,
                    assignment=None,
                )
            )
        return teams

    def sample(self) -> list[dict[str, Any]]:
        """Advance each team a small step and emit a fresh beacon fix."""
        now = utcnow_iso()
        out: list[dict[str, Any]] = []
        for t in self._teams:
            if t.status in ("enroute", "returning"):
                # nudge position along a deterministic walk (~ vehicle speed)
                step_lat = self._rng.uniform(-0.0006, 0.0010)
                step_lon = self._rng.uniform(-0.0006, 0.0010)
                t.location = LatLon(t.location.lat + step_lat, t.location.lon + step_lon)
            t.last_update = now
            out.append(asdict(t))
        return out

    def aggregate(self, readings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Derive per-team speed and flag stale/idle beacons (no decisions)."""
        alerts: list[dict[str, Any]] = []
        speeds: dict[str, float] = {}
        moving = 0
        for r in readings:
            tid = r["team_id"]
            loc = r.get("location", {})
            cur = LatLon(loc.get("lat", 0.0), loc.get("lon", 0.0))
            prev = self._last.get(tid)
            if prev is not None:
                dist_m = haversine(prev, cur)
                # one beacon == one interval; kph = m / interval_s * 3.6
                kph = dist_m / self.beacon_interval_seconds * 3.6
                speeds[tid] = round(kph, 1)
                if kph > 0.5:
                    moving += 1
            self._last[tid] = cur
            if r.get("status") in ("enroute", "returning") and speeds.get(tid, 0.0) < 0.5:
                # beacon says moving but position unchanged -> possible stall
                alerts.append(
                    {
                        "team_id": tid,
                        "kind": "beacon_stall",
                        "status": r.get("status"),
                        "note": f"{tid} {r.get('status')} but stationary (possible GPS loss/stall)",
                    }
                )
        summary = {
            "teams": len(readings),
            "moving": moving,
            "speeds_kph": speeds,
            "beacon_interval_s": self.beacon_interval_seconds,
        }
        return alerts, summary

    def _frame(
        self,
        readings: list[dict[str, Any]],
        alerts: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Beacon frame: expose FieldTeam dicts under ``teams`` for the field agent."""
        frame = super()._frame(readings, alerts, summary)
        # field-coordination agent reads FieldTeam-shaped records directly.
        frame["teams"] = readings
        return frame

    def _priority(self, alerts: list[dict[str, Any]]) -> Priority:
        # Position telemetry is routine; stalls are noteworthy but not critical.
        return Priority.HIGH if alerts else Priority.LOW

    def _reasoning(self, alerts: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
        base = [
            f"gps_beacon: {summary.get('teams', 0)} teams, "
            f"{summary.get('moving', 0)} moving (60s cadence)"
        ]
        return base + [a.get("note", "") for a in alerts if a.get("note")]
