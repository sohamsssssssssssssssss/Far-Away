"""FloodCascadeAgent — inundation-driven route cascade (PRD Step 3, Module A).

Subscribes to :data:`Topic.PREDICTION`, consumes the inundation ``risk_cells``
produced by the prediction tier, and projects which road/bridge segments become
impassable as the flood spreads — emitting a time-sequenced list of
:class:`~disastermind.models.domain.CascadeFailure` objects plus the
route-viability windows on :data:`Topic.CASCADE`.

Key responsibility (PRD Step 3 Module A): "detect when rescue routes get cut off
before teams can return". We expose ``safe_windows`` keyed by segment so the
resource/routing tiers know how long each corridor stays usable, and flag any
route projected to close before a configured round-trip duration.

Heavy spatial libraries (shapely) are imported lazily with a stdlib fallback;
the default path uses only :mod:`math` (haversine), so the package imports and
tests run stdlib-only (PRD HARD RULE 3).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.contracts import Message, MessageType, Module, Priority, Tier, Topic
from ...models.domain import CascadeFailure
from ...models.geo import LatLon

# inundation probability at/above which a segment crossing the cell is unsafe
IMPASSABLE_PROBABILITY = 0.5
# bridges flood-fail at a lower threshold (scour / approach inundation)
BRIDGE_PROBABILITY = 0.35
# assumed rescue round-trip duration (minutes); routes that close sooner = cutoff
DEFAULT_ROUND_TRIP_MIN = 90


@dataclass(frozen=True)
class RoadSegment:
    """A road/bridge segment the cascade model tracks for inundation cutoff."""

    segment_id: str
    a: LatLon
    b: LatLon
    is_bridge: bool = False

    def midpoint(self) -> LatLon:
        return LatLon((self.a.lat + self.b.lat) / 2.0, (self.a.lon + self.b.lon) / 2.0)


def _default_segments() -> list[RoadSegment]:
    """A tiny synthetic road network (fixture) for the dry-run / sample path."""
    return [
        RoadSegment("RD-A1", LatLon(19.0760, 72.8777), LatLon(19.0810, 72.8850)),
        RoadSegment("RD-A2", LatLon(19.0810, 72.8850), LatLon(19.0900, 72.8900)),
        RoadSegment("BR-A1", LatLon(19.0900, 72.8900), LatLon(19.0990, 72.8990), is_bridge=True),
        RoadSegment("RD-A3", LatLon(19.0990, 72.8990), LatLon(19.1100, 72.9100)),
    ]


class FloodCascadeAgent(BaseAgent):
    """Tier-2 specialist projecting flood-driven route failures (PRD Step 3 A)."""

    tier = Tier.SPECIALIST
    decision_authority = True

    def __init__(
        self,
        bus: MessageBus,
        logger=None,
        settings=None,
        segments: list[RoadSegment] | None = None,
    ) -> None:
        super().__init__(
            name="flood_cascade",
            bus=bus,
            logger=logger,
            subscriptions=[Topic.PREDICTION],
        )
        self.settings = settings
        self.segments = segments if segments is not None else _default_segments()
        self.round_trip_min = DEFAULT_ROUND_TRIP_MIN

    # ------------------------------------------------------------------ handle
    def handle(self, message: Message) -> list[Message]:
        """Turn an inundation risk grid into time-sequenced route failures."""
        payload = message.payload or {}
        if payload.get("kind") != "risk":
            return []
        # Only act on flood/cyclone-module predictions (Module A).
        if message.module not in (Module.CYCLONE_FLOOD, Module.ALL):
            return []
        risk_cells = payload.get("risk_cells") or []
        if not risk_cells:
            return []

        failures = self.project_failures(risk_cells)
        if not failures:
            return []

        safe_windows = {f.segment_id: f.viable_until_minute for f in failures}
        cutoffs = [f for f in failures if f.viable_until_minute < self.round_trip_min]

        reasoning = [
            f"ingested {len(risk_cells)} inundation cells from {message.sender}",
            f"projected {len(failures)} segment failures "
            f"(threshold p>={IMPASSABLE_PROBABILITY}, bridge p>={BRIDGE_PROBABILITY})",
        ]
        if cutoffs:
            reasoning.append(
                f"WARNING: {len(cutoffs)} corridor(s) close before the "
                f"{self.round_trip_min}-min rescue round-trip: "
                + ", ".join(c.segment_id for c in cutoffs)
            )

        out = Message(
            sender=self.name,
            recipient="broadcast",
            type=MessageType.ALERT,
            priority=Priority.HIGH if cutoffs else Priority.MEDIUM,
            topic=Topic.CASCADE,
            incident_id=message.incident_id,
            module=Module.CYCLONE_FLOOD,
            reasoning=reasoning,
            payload={
                "kind": "cascade",
                "incident_id": message.incident_id,
                "failures": [dataclasses.asdict(f) for f in failures],
                "safe_windows": safe_windows,
                "cutoff_segments": [c.segment_id for c in cutoffs],
                "round_trip_minutes": self.round_trip_min,
            },
        )
        return [out]

    # -------------------------------------------------------------- modelling
    def project_failures(self, risk_cells: list[dict]) -> list[CascadeFailure]:
        """Project segment failures from inundation risk cells.

        For each road/bridge segment we find the soonest-arriving inundation
        cell whose probability exceeds the (bridge-aware) threshold and that is
        spatially near the segment midpoint. The cell's ``horizon_minutes`` is
        when the corridor becomes impassable; ``viable_until_minute`` is that
        moment minus a small safety buffer.
        """
        try:
            return self._project_shapely(risk_cells)
        except Exception:
            # Any failure in the optional spatial path => deterministic fallback.
            return self._project_haversine(risk_cells)

    def _cell_point(self, cell: dict) -> LatLon | None:
        c = cell.get("centroid")
        if isinstance(c, dict) and "lat" in c and "lon" in c:
            return LatLon(float(c["lat"]), float(c["lon"]))
        return None

    def _project_haversine(self, risk_cells: list[dict]) -> list[CascadeFailure]:
        """Stdlib fallback: nearest-cell-within-radius per segment (PRD R3)."""
        # Cells sorted by horizon so the *earliest* threat wins per segment.
        parsed: list[tuple[int, float, LatLon]] = []
        for cell in risk_cells:
            pt = self._cell_point(cell)
            if pt is None:
                continue
            prob = float(cell.get("probability", 0.0))
            horizon = int(cell.get("horizon_minutes", 0))
            parsed.append((horizon, prob, pt))
        parsed.sort(key=lambda t: t[0])

        # influence radius of a flood cell on a segment (metres)
        influence_m = 400.0
        failures: list[CascadeFailure] = []
        for seg in self.segments:
            mid = seg.midpoint()
            thresh = BRIDGE_PROBABILITY if seg.is_bridge else IMPASSABLE_PROBABILITY
            chosen: tuple[int, float] | None = None
            for horizon, prob, pt in parsed:
                if prob < thresh:
                    continue
                if mid.distance_m(pt) <= influence_m:
                    chosen = (horizon, prob)
                    break  # parsed is horizon-sorted => first match is soonest
            if chosen is None:
                continue
            fails_at, prob = chosen
            buffer = 15 if seg.is_bridge else 10
            viable_until = max(0, fails_at - buffer)
            failures.append(
                CascadeFailure(
                    segment_id=seg.segment_id,
                    fails_at_minute=fails_at,
                    reason="inundation",
                    viable_until_minute=viable_until,
                )
            )
        failures.sort(key=lambda f: f.fails_at_minute)
        return failures

    def _project_shapely(self, risk_cells: list[dict]) -> list[CascadeFailure]:
        """Optional precise path: buffer segment lines and test cell coverage.

        Uses shapely if available (lazy import). Mirrors the haversine logic but
        with true line-buffer intersection. Falls back via the caller's
        try/except if shapely is missing.
        """
        from shapely.geometry import LineString, Point  # type: ignore

        # ~400 m expressed in degrees (rough, adequate at city scale)
        influence_deg = 400.0 / 111_320.0
        parsed: list[tuple[int, float, "Point"]] = []
        for cell in risk_cells:
            pt = self._cell_point(cell)
            if pt is None:
                continue
            parsed.append(
                (
                    int(cell.get("horizon_minutes", 0)),
                    float(cell.get("probability", 0.0)),
                    Point(pt.lon, pt.lat),
                )
            )
        parsed.sort(key=lambda t: t[0])

        failures: list[CascadeFailure] = []
        for seg in self.segments:
            line = LineString([(seg.a.lon, seg.a.lat), (seg.b.lon, seg.b.lat)])
            corridor = line.buffer(influence_deg)
            thresh = BRIDGE_PROBABILITY if seg.is_bridge else IMPASSABLE_PROBABILITY
            chosen: tuple[int, float] | None = None
            for horizon, prob, geom in parsed:
                if prob < thresh:
                    continue
                if corridor.contains(geom):
                    chosen = (horizon, prob)
                    break
            if chosen is None:
                continue
            fails_at, _ = chosen
            buffer = 15 if seg.is_bridge else 10
            failures.append(
                CascadeFailure(
                    segment_id=seg.segment_id,
                    fails_at_minute=fails_at,
                    reason="inundation",
                    viable_until_minute=max(0, fails_at - buffer),
                )
            )
        failures.sort(key=lambda f: f.fails_at_minute)
        return failures
