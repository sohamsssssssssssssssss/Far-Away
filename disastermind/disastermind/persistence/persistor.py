"""State-persistence integration agent (PRD Step 9 — Decision Logging & State).

The :class:`StatePersistor` is the write-behind bridge between the live message
bus and the durable :mod:`disastermind.storage` layer. It is a **Tier-3,
zero-authority** observer (``decision_authority = False``) that subscribes to
every well-known :class:`~disastermind.core.contracts.Topic`, mirrors the message
stream into the four storage repositories, and **emits nothing** — so wiring it
into the DAG can never perturb the load-bearing
``prediction -> resource -> field -> commander -> dispatch`` chain (PRD Step 10).

What it persists, *through* the storage facade
(:class:`disastermind.storage.Storage`):

* **Every** message -> :meth:`ElasticsearchAuditRepo.index_record` so the full
  decision trail is searchable (complements the hash-chained JSONL audit log).
* :data:`Topic.IOT_TELEMETRY` frames -> one or more
  :class:`~disastermind.storage.TelemetryPoint` rows per sensor reading, written
  with :meth:`TimescaleTelemetryRepo.append_many` (PRD Step 6 time-series).
* :data:`Topic.RESOURCE_PLAN` asset state (the deploying assets' positions /
  capacity, carried as ``depots`` on the plan) -> :meth:`PostgisResourceRepo.upsert_asset`
  so spatial asset state is durable for replay / nearest-asset queries (PRD Step 4).

Storage is built via :meth:`Storage.in_memory` by default — **offline, no
network** — matching the rest of the suite (PRD Step 10 graceful degradation).
Pass a pre-built :class:`Storage` (e.g. ``Storage.from_settings(live=True)``) to
write through real backends; each repo still degrades to its in-memory fallback
on its own if its server is unreachable.
"""
from __future__ import annotations

import logging
from typing import Any

from ..audit.decision_log import DecisionLogger
from ..core.agent import BaseAgent
from ..core.bus import MessageBus
from ..core.contracts import Message, Tier, Topic
from ..models.domain import AssetType
from ..storage import Storage, TelemetryPoint

log = logging.getLogger("disastermind.persistence.persistor")

#: Numeric reading fields we lift out of an IOT_TELEMETRY frame as time-series
#: metrics. The IoT gateways (smoke/heat, waterlogging, structural) publish these
#: per-site under ``readings``; mapping each to a TelemetryPoint keeps the
#: hypertable populated for the prediction tier and replay (PRD Step 6).
_TELEMETRY_METRIC_FIELDS = (
    "smoke_ppm",
    "heat_c",
    "water_level_m",
    "microstrain",
    "tilt_deg",
    "accel_g",
)


def all_topics() -> list[str]:
    """Return every public ``Topic.*`` string constant (introspected).

    Introspecting :class:`~disastermind.core.contracts.Topic` (rather than
    restating a list) keeps the persistor correct if the foundation grows a
    topic — exactly the pattern the observability collector uses.
    """
    return [
        getattr(Topic, name)
        for name in vars(Topic)
        if not name.startswith("_") and isinstance(getattr(Topic, name), str)
    ]


class StatePersistor(BaseAgent):
    """Write-behind persistence of the live message stream (PRD Step 9).

    Subscribes to all topics, mirrors each message into the durable storage
    repos, and returns no outbound messages (read-only, zero authority).
    """

    tier = Tier.EDGE
    decision_authority = False  # PRD Step 8 — edge persistence never decides/emits.

    def __init__(
        self,
        bus: MessageBus,
        logger: DecisionLogger | None = None,
        storage: Storage | None = None,
        name: str = "persistence.state",
    ) -> None:
        # Offline by default — Storage.in_memory() contacts no external service.
        self.storage: Storage = storage or Storage.in_memory()
        # Lightweight counters so tests / ops can confirm the write path is live.
        self.audit_writes: int = 0
        self.telemetry_writes: int = 0
        self.asset_writes: int = 0
        super().__init__(name=name, bus=bus, logger=logger, subscriptions=all_topics())

    # ------------------------------------------------------------------ hooks
    def handle(self, message: Message) -> list[Message]:
        """Persist one observed message; return nothing (read-only, Step 10).

        Failures in any single repo are swallowed and logged so a storage hiccup
        can never break the bus fan-out or the load-bearing chain (PRD Step 10).
        """
        self._index_audit(message)
        if message.topic == Topic.IOT_TELEMETRY:
            self._persist_telemetry(message)
        elif message.topic == Topic.RESOURCE_PLAN:
            self._persist_assets(message)
        return []

    # --------------------------------------------------------------- audit (all)
    def _index_audit(self, message: Message) -> None:
        try:
            self.storage.audit.index_record(message)
            self.audit_writes += 1
        except Exception:  # pragma: no cover - defensive (Step 10)
            log.exception("persistor: audit index failed for %s", message.id)

    # ------------------------------------------------------------- telemetry (IoT)
    def _persist_telemetry(self, message: Message) -> None:
        """Map an IOT_TELEMETRY frame's readings to TelemetryPoints and append."""
        try:
            points = self._frame_to_points(message)
            if points:
                self.storage.telemetry.append_many(points)
                self.telemetry_writes += len(points)
        except Exception:  # pragma: no cover - defensive (Step 10)
            log.exception("persistor: telemetry append failed for %s", message.id)

    def _frame_to_points(self, message: Message) -> list[TelemetryPoint]:
        payload = message.payload or {}
        ts = payload.get("sampled_at") or message.timestamp
        kind = str(payload.get("kind", "telemetry"))
        readings = payload.get("readings") or []
        points: list[TelemetryPoint] = []
        for r in readings:
            if not isinstance(r, dict):
                continue
            sensor_id = str(
                r.get("site_id") or r.get("team_id") or r.get("sensor_id") or "unknown"
            )
            base_meta = {
                "kind": kind,
                "zone": r.get("zone"),
                "gateway": payload.get("gateway"),
                "incident_id": message.incident_id,
                "module": message.module.value,
            }
            # Lift each numeric sensor field into its own metric row.
            emitted = False
            for fld in _TELEMETRY_METRIC_FIELDS:
                if fld in r and self._is_number(r[fld]):
                    points.append(
                        TelemetryPoint(
                            sensor_id=sensor_id,
                            metric=fld,
                            value=float(r[fld]),
                            ts=ts,
                            meta={k: v for k, v in base_meta.items() if v is not None},
                        )
                    )
                    emitted = True
            if not emitted:
                # GPS-beacon / status-only readings carry no scalar sensor field;
                # record a position fix so the hypertable still lands a row.
                loc = r.get("location") or {}
                meta = {k: v for k, v in base_meta.items() if v is not None}
                if isinstance(loc, dict):
                    meta = {
                        **meta,
                        "lat": loc.get("lat"),
                        "lon": loc.get("lon"),
                        "status": r.get("status"),
                    }
                points.append(
                    TelemetryPoint(
                        sensor_id=sensor_id,
                        metric=kind,
                        value=1.0,
                        ts=ts,
                        meta={k: v for k, v in meta.items() if v is not None},
                    )
                )
        return points

    @staticmethod
    def _is_number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    # ----------------------------------------------------- assets (RESOURCE_PLAN)
    def _persist_assets(self, message: Message) -> None:
        """Upsert deploying-asset spatial state carried on a RESOURCE_PLAN.

        The resource allocator attaches each order's asset position/capacity under
        ``depots`` (the frozen DeploymentOrder carries no coordinates). We persist
        those as :class:`~disastermind.models.domain.Asset` rows via PostGIS so
        spatial asset state survives for replay / nearest-asset queries.
        """
        payload = message.payload or {}
        depots = payload.get("depots") or []
        for depot in depots:
            asset = self._depot_to_asset(depot)
            if asset is None:
                continue
            try:
                self.storage.resources.upsert_asset(asset)
                self.asset_writes += 1
            except Exception:  # pragma: no cover - defensive (Step 10)
                log.exception("persistor: asset upsert failed for %r", depot)

    @staticmethod
    def _depot_to_asset(depot: Any) -> dict[str, Any] | None:
        """Coerce a RESOURCE_PLAN depot dict into the repo's Asset dict shape.

        ``PostgisResourceRepo.upsert_asset`` accepts a dict with ``asset_id``,
        ``type``, ``location`` and optional ``capacity`` — exactly what we build
        here. Assets without an id/location are skipped (nothing to key on).
        """
        if not isinstance(depot, dict):
            return None
        asset_id = depot.get("vehicle_id") or depot.get("asset_id")
        location = depot.get("depot") or depot.get("location")
        if not asset_id or not isinstance(location, dict):
            return None
        return {
            "asset_id": str(asset_id),
            # Type is not carried on depots; default to NDRF_TEAM (a valid
            # AssetType) so the row is well-formed for spatial queries.
            "type": str(depot.get("type") or AssetType.NDRF_TEAM.value),
            "location": {
                "lat": float(location.get("lat", 0.0)),
                "lon": float(location.get("lon", 0.0)),
            },
            "capacity": int(depot.get("capacity", 0) or 0),
            "available": bool(depot.get("available", True)),
        }
