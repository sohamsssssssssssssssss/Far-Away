"""Domain model — the nouns every agent exchanges inside message payloads.

These are plain dataclasses (serialisable via ``dataclasses.asdict``) so they
ride inside :class:`~disastermind.core.contracts.Message` payloads cleanly and
map onto PostgreSQL+PostGIS rows. Vulnerability weighting (PRD Step 4 equity
constraint, Step 5 priority order) is first-class here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .geo import LatLon


# --------------------------------------------------------------------------- events
class EventKind(str, Enum):
    CYCLONE = "cyclone"
    FLOOD = "flood"
    EARTHQUAKE = "earthquake"
    URBAN_FIRE = "urban_fire"
    STRUCTURAL_COLLAPSE = "structural_collapse"


@dataclass
class DisasterEvent:
    incident_id: str
    kind: EventKind
    epicentre: LatLon
    severity: float  # magnitude / category / fire intensity, domain-scaled
    detected_at: str  # ISO 8601
    source: str = ""  # e.g. "USGS", "IMD", "FIRMS"
    meta: dict = field(default_factory=dict)


# --------------------------------------------------------------------- vulnerability
@dataclass
class VulnerabilityProfile:
    """Per-zone equity weighting inputs (PRD Step 4)."""

    elderly_density: float = 0.0
    hospital_proximity: float = 0.0
    road_accessibility: float = 1.0
    informal_settlement_density: float = 0.0
    mobility_impaired: int = 0
    children: int = 0
    hospitalised: int = 0

    def weight(self) -> float:
        """Composite vulnerability multiplier (>= 1.0). Higher == prioritise."""
        return 1.0 + self.elderly_density + self.informal_settlement_density + (
            1.0 - self.road_accessibility
        )


@dataclass
class PopulationCell:
    cell_id: str
    centroid: LatLon
    population: int
    vulnerability: VulnerabilityProfile = field(default_factory=VulnerabilityProfile)


# ------------------------------------------------------------------- predictions out
@dataclass
class RiskCell:
    """Generic per-cell risk output usable by all three modules."""

    cell_id: str
    centroid: LatLon
    probability: float  # 0..1 (inundation / collapse / burn probability)
    horizon_minutes: int  # T+6h => 360, etc.
    population_at_risk: int = 0
    shap: dict = field(default_factory=dict)  # explainability (PRD Step 9)


@dataclass
class BuildingImpact:
    building_id: str
    location: LatLon
    collapse_probability: float
    estimated_trapped: int
    construction: str = "unknown"  # kutcha / pucca / RCC


@dataclass
class FireFront:
    horizon_minutes: int  # T+15/30/60
    perimeter: list[LatLon]
    critical_infrastructure: list[str] = field(default_factory=list)


@dataclass
class CascadeFailure:
    """Roads/bridges projected to fail and the window they remain viable."""

    segment_id: str
    fails_at_minute: int
    reason: str  # "inundation" | "high_mmi" | "fire_path"
    viable_until_minute: int


# ----------------------------------------------------------------------- resources
class AssetType(str, Enum):
    BOAT = "boat"
    HELICOPTER = "helicopter"
    NDRF_TEAM = "ndrf_team"
    SDRF_TEAM = "sdrf_team"
    MEDICAL_UNIT = "medical_unit"
    FIRE_ENGINE = "fire_engine"
    USAR_TEAM = "usar_team"


@dataclass
class Asset:
    asset_id: str
    type: AssetType
    location: LatLon
    capacity: int = 0
    available: bool = True
    fuel_pct: float = 100.0


@dataclass
class FieldTeam:
    team_id: str
    asset_type: AssetType
    location: LatLon
    last_update: str  # ISO 8601, 60 s GPS beacon (PRD Step 6)
    status: str = "idle"  # idle | enroute | onsite | returning
    assignment: str | None = None


@dataclass
class Shelter:
    shelter_id: str
    location: LatLon
    capacity: int
    occupancy: int = 0

    @property
    def fill_ratio(self) -> float:
        return self.occupancy / self.capacity if self.capacity else 1.0


# ------------------------------------------------------------------- plans / orders
@dataclass
class DeploymentOrder:
    order_id: str
    asset_id: str
    target_cell: str
    priority: int  # 1..5
    reason: str
    eta_minutes: float | None = None


@dataclass
class ResourceGap:
    zone_id: str
    asset_type: AssetType
    shortfall: int
    note: str = ""


@dataclass
class EvacRoute:
    route_id: str
    vehicle_id: str
    waypoints: list[LatLon]
    population_class: str  # mobility_impaired | elderly | children | hospitalised | general
    shelter_id: str
    depart_after_minute: int = 0
    avoid_reason: str = ""


@dataclass
class EscalationReport:
    """Generated when a decision exceeds autonomous authority (PRD Step 7)."""

    report_id: str
    trigger: str  # EscalationTrigger value
    summary: str
    recommended_action: str
    timeout_seconds: int
    human_only: bool
    supporting: dict = field(default_factory=dict)
