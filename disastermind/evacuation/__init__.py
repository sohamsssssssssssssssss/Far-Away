"""Evacuation decision layer (Session B / system lane).

Turns a hazard *forecast* into an evacuation *decision*: given a zone's
population, road egress capacity and shelters, how long does it take to empty the
zone (clearance time), and — compared against the forecast's warning lead time —
**by when must the evacuation order be issued**, or is the zone not clearable in
time at all? This is what makes DisasterMind an evacuation system rather than a
prediction.

Scope (honesty bar): evacuation framing applies to **cyclone / flood / fire**
only. Earthquakes are impact-triage + early-warning automation, not evacuation
forecasting — do not route quake hazards through this layer.
"""
from __future__ import annotations

from .clearance import (
    ClearanceEstimate,
    EvacuationDecision,
    contraflow_egress,
    decide,
    egress_capacity_from_roads,
    estimate_clearance,
)
from .decision import (
    BELOW_BREAKEVEN_HOLD,
    NO_ACTIONABLE_WARNING,
    NOT_CLEARABLE_VERTICAL,
    ORDER_BY_DEADLINE,
    ZoneEvacuationDecision,
    decide_zone_evacuation,
)
from .dissemination import (
    CHANNELS,
    ComplianceOptimum,
    DisseminationAssessment,
    assess_dissemination,
    combined_reach,
    compliance_curve,
    compliance_given_reached,
)
from .risk_trajectory import Horizon, RiskTrajectory, actionable_lead_hours, far_at_lead
from .tradeoff import (
    CohortTradeoff,
    EvacuationTradeoff,
    break_even_probability,
    evacuation_tradeoff,
)
from .vulnerability import (
    DEFAULT_COHORTS,
    Cohort,
    CohortPlan,
    PhasedEvacuation,
    plan_phased_evacuation,
)

__all__ = [
    "RiskTrajectory",
    "Horizon",
    "actionable_lead_hours",
    "ClearanceEstimate",
    "estimate_clearance",
    "egress_capacity_from_roads",
    "EvacuationDecision",
    "decide",
    "Cohort",
    "DEFAULT_COHORTS",
    "CohortPlan",
    "PhasedEvacuation",
    "plan_phased_evacuation",
    "far_at_lead",
    "CHANNELS",
    "combined_reach",
    "compliance_given_reached",
    "compliance_curve",
    "ComplianceOptimum",
    "assess_dissemination",
    "DisseminationAssessment",
    "contraflow_egress",
    "break_even_probability",
    "evacuation_tradeoff",
    "EvacuationTradeoff",
    "CohortTradeoff",
    "decide_zone_evacuation",
    "ZoneEvacuationDecision",
    "ORDER_BY_DEADLINE",
    "NOT_CLEARABLE_VERTICAL",
    "BELOW_BREAKEVEN_HOLD",
    "NO_ACTIONABLE_WARNING",
]
