"""Chemical-storage registry fixture + hazmat geometry (PRD Step 3, Module C).

The HazmatAgent cross-references a fire location against a registry of fixed
chemical-storage sites and derives a downwind exclusion zone from the stored
material's class and the prevailing wind. We ship a small, India-flavoured
fixture (industrial belts around Mumbai/Visakhapatnam/Ahmedabad) so the e2e
pipeline runs offline — no network, no DB (PRD HARD RULE 4).

Exclusion radii are coarse public-safety defaults loosely modelled on the
ERG (Emergency Response Guidebook) "protective action distance" idea: a base
isolation radius scaled by toxicity/flammability class. These are NOT operational
ERG values — they are deterministic stand-ins for an offline demo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...models.geo import LatLon


class HazardClass:
    """Coarse hazardous-material classes with default protective distances."""

    TOXIC_GAS = "toxic_gas"  # e.g. chlorine, ammonia — large downwind plume
    FLAMMABLE_LIQUID = "flammable_liquid"  # petrol, naphtha — pool fire / BLEVE
    FLAMMABLE_GAS = "flammable_gas"  # LPG — vapour-cloud / BLEVE radius
    CORROSIVE = "corrosive"  # acids — moderate vapour hazard
    OXIDISER = "oxidiser"  # ammonium nitrate — blast on detonation
    EXPLOSIVE = "explosive"  # ordnance / AN at scale — blast radius

    #: base isolation radius (metres) and downwind multiplier per class.
    BASE_RADIUS_M: dict[str, float] = {
        TOXIC_GAS: 800.0,
        FLAMMABLE_LIQUID: 300.0,
        FLAMMABLE_GAS: 500.0,
        CORROSIVE: 200.0,
        OXIDISER: 600.0,
        EXPLOSIVE: 1000.0,
    }
    #: how much further the plume extends downwind vs. crosswind/upwind.
    DOWNWIND_MULTIPLIER: dict[str, float] = {
        TOXIC_GAS: 4.0,
        FLAMMABLE_LIQUID: 1.5,
        FLAMMABLE_GAS: 2.5,
        CORROSIVE: 2.0,
        OXIDISER: 1.2,
        EXPLOSIVE: 1.0,  # blast is roughly isotropic
    }
    #: which specialist asset the site demands (maps to AssetType-ish strings).
    SPECIALIST: dict[str, str] = {
        TOXIC_GAS: "hazmat_chem_team",
        FLAMMABLE_LIQUID: "foam_fire_engine",
        FLAMMABLE_GAS: "hazmat_chem_team",
        CORROSIVE: "hazmat_chem_team",
        OXIDISER: "bomb_disposal_unit",
        EXPLOSIVE: "bomb_disposal_unit",
    }


@dataclass(frozen=True)
class ChemicalSite:
    """A fixed chemical-storage facility (registry row)."""

    site_id: str
    name: str
    location: LatLon
    hazard_class: str
    material: str
    quantity_tonnes: float = 0.0
    meta: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- fixture
_REGISTRY: list[ChemicalSite] = [
    ChemicalSite(
        "CHM-MUM-01", "Trombay LPG terminal", LatLon(19.0176, 72.9181),
        HazardClass.FLAMMABLE_GAS, "LPG", quantity_tonnes=4200.0,
    ),
    ChemicalSite(
        "CHM-MUM-02", "Chembur chlorine plant", LatLon(19.0510, 72.9000),
        HazardClass.TOXIC_GAS, "chlorine", quantity_tonnes=120.0,
    ),
    ChemicalSite(
        "CHM-VSK-01", "Visakhapatnam styrene unit", LatLon(17.7510, 83.2090),
        HazardClass.TOXIC_GAS, "styrene", quantity_tonnes=1800.0,
    ),
    ChemicalSite(
        "CHM-AMD-01", "Ankleshwar acid storage", LatLon(21.6260, 73.0030),
        HazardClass.CORROSIVE, "sulphuric_acid", quantity_tonnes=600.0,
    ),
    ChemicalSite(
        "CHM-MUM-03", "Wadala naphtha depot", LatLon(19.0170, 72.8580),
        HazardClass.FLAMMABLE_LIQUID, "naphtha", quantity_tonnes=2500.0,
    ),
    ChemicalSite(
        "CHM-VSK-02", "Vizag ammonium nitrate yard", LatLon(17.6900, 83.2180),
        HazardClass.OXIDISER, "ammonium_nitrate", quantity_tonnes=900.0,
    ),
]


def default_registry() -> list[ChemicalSite]:
    """Return a copy of the bundled chemical-storage registry (fixture)."""
    return list(_REGISTRY)


def sites_near(
    point: LatLon, radius_m: float, registry: list[ChemicalSite] | None = None
) -> list[tuple[ChemicalSite, float]]:
    """Registry sites within ``radius_m`` of ``point``, paired with distance."""
    reg = registry if registry is not None else _REGISTRY
    hits: list[tuple[ChemicalSite, float]] = []
    for site in reg:
        d = point.distance_m(site.location)
        if d <= radius_m:
            hits.append((site, d))
    hits.sort(key=lambda t: t[1])
    return hits


def _bearing_deg(origin: LatLon, dest: LatLon) -> float:
    """Initial great-circle bearing from ``origin`` to ``dest`` (deg from N)."""
    lat1, lat2 = math.radians(origin.lat), math.radians(dest.lat)
    dlon = math.radians(dest.lon - origin.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _offset(point: LatLon, bearing_deg: float, distance_m: float) -> LatLon:
    """Project ``point`` ``distance_m`` along ``bearing_deg`` (equirectangular)."""
    m_per_deg = 111_320.0
    dlat = (distance_m * math.cos(math.radians(bearing_deg))) / m_per_deg
    coslat = math.cos(math.radians(point.lat)) or 1e-6
    dlon = (distance_m * math.sin(math.radians(bearing_deg))) / (m_per_deg * coslat)
    return LatLon(point.lat + dlat, point.lon + dlon)


@dataclass
class ExclusionZone:
    """An asymmetric (downwind-stretched) exclusion zone around a hazmat site."""

    site_id: str
    centre: LatLon
    isolation_radius_m: float  # crosswind / minimum keep-out radius
    downwind_radius_m: float  # extent in the wind-blown direction
    wind_to_bearing_deg: float  # direction the plume travels (deg from N)
    hazard_class: str
    material: str
    specialist: str
    polygon: list[LatLon] = field(default_factory=list)


def compute_exclusion_zone(
    site: ChemicalSite, wind_from_deg: float, wind_speed_ms: float = 5.0
) -> ExclusionZone:
    """Compute an exclusion zone from material class + wind (PRD Step 3 C).

    Args:
        site: the burning chemical site.
        wind_from_deg: meteorological wind direction (the direction the wind is
            coming *from*, deg from north — IMD/Open-Meteo convention).
        wind_speed_ms: wind speed; faster wind stretches the plume downwind.

    The plume travels *toward* ``wind_from_deg + 180``. The downwind radius is
    the base radius times a per-class multiplier, further stretched by wind
    speed (clamped). The crosswind isolation radius is the base radius. The
    polygon is a simple wind-rose teardrop: a ring of points whose radius
    interpolates between isolation (upwind/crosswind) and downwind (lee side).
    """
    base = HazardClass.BASE_RADIUS_M.get(site.hazard_class, 300.0)
    dmult = HazardClass.DOWNWIND_MULTIPLIER.get(site.hazard_class, 1.5)
    specialist = HazardClass.SPECIALIST.get(site.hazard_class, "hazmat_chem_team")

    # Quantity bumps the radius logarithmically (more material => bigger zone).
    qty_factor = 1.0 + math.log10(max(site.quantity_tonnes, 1.0) + 1.0) / 5.0
    isolation = base * qty_factor
    # Wind speed stretch: 1.0 at calm, up to ~2x at gale (clamped 0..1 over 20 m/s).
    wind_stretch = 1.0 + min(max(wind_speed_ms, 0.0), 20.0) / 20.0
    downwind = isolation * dmult * wind_stretch

    plume_to = (wind_from_deg + 180.0) % 360.0

    polygon: list[LatLon] = []
    for step in range(0, 360, 30):
        # angular distance of this ring point from the downwind direction
        delta = abs(((step - plume_to + 180.0) % 360.0) - 180.0)
        # cosine taper: full downwind radius at delta=0, isolation at delta>=90
        lobe = max(0.0, math.cos(math.radians(min(delta, 90.0))))
        r = isolation + (downwind - isolation) * lobe
        polygon.append(_offset(site.location, float(step), r))

    return ExclusionZone(
        site_id=site.site_id,
        centre=site.location,
        isolation_radius_m=isolation,
        downwind_radius_m=downwind,
        wind_to_bearing_deg=plume_to,
        hazard_class=site.hazard_class,
        material=site.material,
        specialist=specialist,
        polygon=polygon,
    )
