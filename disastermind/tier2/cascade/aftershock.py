"""EarthquakeCascadeAgent — aftershock-driven structural cascade (PRD Step 3 B).

Subscribes to :data:`Topic.PREDICTION`, consumes the earthquake-impact payload
produced by :class:`~disastermind.tier2.prediction.agents.EarthquakeImpactAgent`
(``BuildingImpact`` list + rescue-priority ``risk_cells``), and projects which
already-damaged structures / access segments are likely to fail *further* as
aftershocks strike — emitting a time-sequenced list of
:class:`~disastermind.models.domain.CascadeFailure` objects plus the
M5.0+ aftershock probability windows on :data:`Topic.CASCADE`.

Key responsibility (PRD Step 3 Module B): a mainshock leaves buildings in a
weakened state; an aftershock can finish them off and cut the rescue corridors
that pass beside them. We use the modified Omori-Utsu law (:mod:`.omori`) to
forecast the probability of a damaging (M>=5.0) aftershock at 24/48/72 h, and
flag the high-collapse-probability segments whose ``viable_until`` window closes
before the projected aftershock horizon.

Pure-Python / stdlib only (the Omori model is analytic, :mod:`.omori`), so the
package imports and the test-suite runs with stdlib only (PRD HARD RULE 3).
"""
from __future__ import annotations

import dataclasses

from ...core.agent import BaseAgent
from ...core.bus import MessageBus
from ...core.contracts import Message, MessageType, Module, Priority, Tier, Topic
from ...models.domain import CascadeFailure
from .omori import OmoriParams, fit_params, probability_by_horizon

# Collapse probability at/above which a building/segment is treated as a likely
# aftershock cascade failure (already-weakened structure).
AFTERSHOCK_COLLAPSE_PROBABILITY = 0.35
# Aftershock magnitude of interest for the M>=M0 probability forecast.
DAMAGING_AFTERSHOCK_MAG = 5.0
# Horizons (hours) for the aftershock probability windows (PRD Step 3 B).
AFTERSHOCK_HORIZONS_HOURS = (24, 48, 72)
# A weakened structure is treated as viable for rescue only until the first
# horizon at which a damaging aftershock is more likely than not.
MORE_LIKELY_THAN_NOT = 0.5


class EarthquakeCascadeAgent(BaseAgent):
    """Tier-2 specialist projecting aftershock-driven failures (PRD Step 3 B)."""

    tier = Tier.SPECIALIST
    decision_authority = True

    def __init__(
        self,
        bus: MessageBus,
        logger=None,
        settings=None,
    ) -> None:
        super().__init__(
            name="earthquake_cascade",
            bus=bus,
            logger=logger,
            subscriptions=[Topic.PREDICTION],
        )
        self.settings = settings

    # ------------------------------------------------------------------ handle
    def handle(self, message: Message) -> list[Message]:
        """Turn an earthquake impact assessment into aftershock cascade failures."""
        payload = message.payload or {}
        if payload.get("kind") != "risk":
            return []
        # Only act on earthquake-module predictions (Module B).
        if message.module not in (Module.EARTHQUAKE, Module.ALL):
            return []

        buildings = payload.get("buildings") or []
        risk_cells = payload.get("risk_cells") or []
        if not buildings and not risk_cells:
            return []

        magnitude, depth_km = self._mainshock_params(message, payload, buildings)
        params = fit_params(magnitude, depth_km)
        horizon_probs = probability_by_horizon(
            params,
            m0=DAMAGING_AFTERSHOCK_MAG,
            horizons_hours=AFTERSHOCK_HORIZONS_HOURS,
        )
        viable_until_min = self._viable_until_minutes(horizon_probs)

        failures = self._project_failures(buildings, risk_cells, viable_until_min)
        if not failures:
            return []

        safe_windows = {f.segment_id: f.viable_until_minute for f in failures}
        peak_aftershock_p = max(horizon_probs.values(), default=0.0)

        reasoning = [
            f"earthquake cascade for M{magnitude:.1f} depth={depth_km:.0f}km mainshock",
            "Omori-Utsu aftershock forecast P(>=1 M{:.1f}+): ".format(
                DAMAGING_AFTERSHOCK_MAG
            )
            + ", ".join(f"{h}h={p:.2f}" for h, p in sorted(horizon_probs.items())),
            f"projected {len(failures)} weakened segment(s) at aftershock-collapse "
            f"risk (collapse p>={AFTERSHOCK_COLLAPSE_PROBABILITY})",
        ]

        out = Message(
            sender=self.name,
            recipient="broadcast",
            type=MessageType.ALERT,
            priority=Priority.CRITICAL if peak_aftershock_p >= 0.5 else Priority.HIGH,
            topic=Topic.CASCADE,
            incident_id=message.incident_id,
            module=Module.EARTHQUAKE,
            reasoning=reasoning,
            payload={
                "kind": "cascade",
                "incident_id": message.incident_id,
                "failures": [dataclasses.asdict(f) for f in failures],
                "safe_windows": safe_windows,
                "cutoff_segments": [f.segment_id for f in failures],
                "aftershock_probability": {str(h): round(p, 4) for h, p in horizon_probs.items()},
                "aftershock_magnitude": DAMAGING_AFTERSHOCK_MAG,
            },
        )
        return [out]

    # -------------------------------------------------------------- modelling
    @staticmethod
    def _mainshock_params(
        message: Message, payload: dict, buildings: list[dict]
    ) -> tuple[float, float]:
        """Recover mainshock magnitude/depth for the Omori fit.

        The prediction payload does not carry the raw magnitude directly, so we
        accept it from a few conventional locations and otherwise infer a
        conservative magnitude from the peak collapse probability observed.
        """
        for src in (payload, payload.get("event") or {}, (payload.get("event") or {}).get("meta") or {}):
            if not isinstance(src, dict):
                continue
            mag = src.get("magnitude") or src.get("severity")
            if mag is not None:
                try:
                    magnitude = float(mag)
                except (TypeError, ValueError):
                    continue
                depth = src.get("depth_km", 10.0)
                try:
                    depth_km = float(depth)
                except (TypeError, ValueError):
                    depth_km = 10.0
                return magnitude, depth_km

        # Fallback: infer a plausible mainshock magnitude from impact severity.
        peak_collapse = 0.0
        for b in buildings:
            if isinstance(b, dict):
                peak_collapse = max(peak_collapse, float(b.get("collapse_probability", 0.0) or 0.0))
        # Heavier observed damage -> larger inferred mainshock (5.0 .. 7.0).
        magnitude = 5.0 + 2.0 * max(0.0, min(1.0, peak_collapse))
        return magnitude, 10.0

    @staticmethod
    def _viable_until_minutes(horizon_probs: dict[int, float]) -> int:
        """Minutes until a damaging aftershock becomes more likely than not.

        Walks the cumulative horizon probabilities and returns the first horizon
        (converted to minutes) at which P(>=1 damaging aftershock) crosses 0.5.
        If it never does within the forecast window, returns the last horizon.
        """
        for hours in sorted(horizon_probs):
            if horizon_probs[hours] >= MORE_LIKELY_THAN_NOT:
                # Buffer: warn 30 min before the crossing horizon.
                return max(0, hours * 60 - 30)
        last = max(horizon_probs, default=24)
        return last * 60

    def _project_failures(
        self,
        buildings: list[dict],
        risk_cells: list[dict],
        viable_until_min: int,
    ) -> list[CascadeFailure]:
        """Flag already-weakened structures/zones as aftershock cascade failures.

        A building whose mainshock collapse probability exceeds the threshold is
        treated as liable to finish collapsing in an aftershock; its access
        corridor stays viable only until the projected aftershock window.
        """
        failures: list[CascadeFailure] = []
        seen: set[str] = set()

        for b in buildings:
            if not isinstance(b, dict):
                continue
            prob = float(b.get("collapse_probability", 0.0) or 0.0)
            if prob < AFTERSHOCK_COLLAPSE_PROBABILITY:
                continue
            seg_id = str(b.get("building_id") or f"bld-{len(failures)}")
            if seg_id in seen:
                continue
            seen.add(seg_id)
            # Sooner-failing for more-fragile structures.
            fails_at = int(round(viable_until_min * (1.0 - min(1.0, prob)) + 30))
            failures.append(
                CascadeFailure(
                    segment_id=seg_id,
                    fails_at_minute=fails_at,
                    reason="high_mmi",
                    viable_until_minute=max(0, min(fails_at - 15, viable_until_min)),
                )
            )

        # Also flag high-collapse rescue-priority zones (cell-level cascade).
        for c in risk_cells:
            if not isinstance(c, dict):
                continue
            prob = float(c.get("probability", 0.0) or 0.0)
            if prob < AFTERSHOCK_COLLAPSE_PROBABILITY:
                continue
            seg_id = str(c.get("cell_id") or f"zone-{len(failures)}")
            if seg_id in seen:
                continue
            seen.add(seg_id)
            fails_at = int(round(viable_until_min * (1.0 - min(1.0, prob)) + 30))
            failures.append(
                CascadeFailure(
                    segment_id=seg_id,
                    fails_at_minute=fails_at,
                    reason="high_mmi",
                    viable_until_minute=max(0, min(fails_at - 15, viable_until_min)),
                )
            )

        failures.sort(key=lambda f: f.fails_at_minute)
        return failures
