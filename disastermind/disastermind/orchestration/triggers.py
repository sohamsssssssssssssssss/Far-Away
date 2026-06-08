"""Trigger & activation logic (PRD Group A, Step 1).

Pure, side-effect-free predicates so activation is trivially unit-testable. The
runtime feeds a :class:`Signals` snapshot (assembled from Tier 3 feeds/IoT) into
:func:`should_activate` each cycle; a non-None result flips ``disaster_active``.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.contracts import Module

# --- thresholds straight from PRD Step 1 -----------------------------------
GAUGE_DANGER_PCT = 75.0          # river gauge >= 75% of danger level (Module A)
WATERLOG_ZONE_THRESHOLD = 3      # waterlogging breach in 3+ zones (Module A)
SEISMIC_MIN_MAGNITUDE = 4.5      # USGS/NCS M4.5+ (Module B)
BRIGADE_CALL_THRESHOLD = 3       # 3+ brigade calls in a grid zone / 10 min (Module C)


@dataclass
class Signals:
    """A snapshot of the activation-relevant signals across all modules."""

    # Module A — cyclone / flood
    imd_cyclone_alert: bool = False          # Cyclonic Storm / Deep Depression issued
    river_gauge_pct_of_danger: float = 0.0   # max observed % of danger level
    dam_discharge_ordered: bool = False
    waterlogging_breach_zones: int = 0
    hours_to_landfall: float | None = None

    # Module B — earthquake
    max_seismic_magnitude: float = 0.0

    # Module C — urban fire / collapse
    fire_brigade_calls_in_zone_10min: int = 0
    smoke_heat_cluster: bool = False
    firms_thermal_anomaly: bool = False
    social_collapse_cluster: bool = False


@dataclass
class ActivationDecision:
    module: Module
    reason: str
    lead_time_note: str


def module_a_active(s: Signals) -> tuple[bool, str]:
    """Cyclone/Flood — activates 72h before projected landfall."""
    if s.imd_cyclone_alert:
        return True, "IMD Cyclonic Storm / Deep Depression alert"
    if s.river_gauge_pct_of_danger >= GAUGE_DANGER_PCT:
        return True, f"river gauge at {s.river_gauge_pct_of_danger:.0f}% of danger level"
    if s.dam_discharge_ordered:
        return True, "dam discharge orders issued"
    if s.waterlogging_breach_zones >= WATERLOG_ZONE_THRESHOLD:
        return True, f"urban waterlogging breach in {s.waterlogging_breach_zones} zones"
    return False, ""


def module_b_active(s: Signals) -> tuple[bool, str]:
    """Earthquake — activates within 90s of an M4.5+ detection."""
    if s.max_seismic_magnitude >= SEISMIC_MIN_MAGNITUDE:
        return True, f"seismic event M{s.max_seismic_magnitude:.1f} (>= M{SEISMIC_MIN_MAGNITUDE})"
    return False, ""


def module_c_active(s: Signals) -> tuple[bool, str]:
    """Urban fire / collapse — activates immediately on any threshold breach."""
    if s.fire_brigade_calls_in_zone_10min >= BRIGADE_CALL_THRESHOLD:
        return True, f"{s.fire_brigade_calls_in_zone_10min} brigade calls in one grid zone / 10 min"
    if s.smoke_heat_cluster:
        return True, "IoT smoke/heat sensor cluster threshold exceeded"
    if s.firms_thermal_anomaly:
        return True, "NASA FIRMS thermal anomaly detected"
    if s.social_collapse_cluster:
        return True, "social-media NLP geo-tagged collapse keyword cluster"
    return False, ""


# Precedence by time-criticality: earthquake (90s) > fire (immediate) > flood (72h lead).
_PREDICATES = [
    (module_b_active, Module.EARTHQUAKE, "activate within 90 seconds of detection"),
    (module_c_active, Module.FIRE_COLLAPSE, "activate immediately on threshold breach"),
    (module_a_active, Module.CYCLONE_FLOOD, "activate 72 hours before projected landfall"),
]


def should_activate(signals: Signals) -> Module | None:
    """Return the highest time-criticality module to activate, or None."""
    for fn, module, _lead in _PREDICATES:
        active, _reason = fn(signals)
        if active:
            return module
    return None


def activation_report(signals: Signals) -> list[ActivationDecision]:
    """All modules currently triggered (disasters can co-occur)."""
    out: list[ActivationDecision] = []
    for fn, module, lead in _PREDICATES:
        active, reason = fn(signals)
        if active:
            out.append(ActivationDecision(module=module, reason=reason, lead_time_note=lead))
    return out
