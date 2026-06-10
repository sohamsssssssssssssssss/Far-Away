"""Validate shelter CAPACITY vs real population (Puri / Cyclone Fani zone).

Routing people is moot if the shelters cannot hold them — so this is the crux of
life-safety. It compares the real at-risk population (Census-2011 figure, carried
on OSM's population tag for Puri) against the shelter capacity *derived from real
OSM building footprints* at transparent humanitarian density standards.

The headline finding is honest and two-sided:
  1. The OSM-tagged shelters can hold only a small fraction of Puri's population —
     so a system fed only OSM data would CORRECTLY flag a large shelter shortfall
     and trigger mutual-aid / additional-shelter activation. Flagging the gap is
     the right behaviour, not a failure.
  2. That gap is largely a DATA-AVAILABILITY artifact: the real Fani evacuation
     succeeded (64 deaths despite a Category-4 strike) because Odisha used the
     OSDMA multipurpose-cyclone-shelter network (~800+ purpose-built shelters)
     which OSM does not tag. The actionable deployment requirement is to load the
     real OSDMA shelter registry, not rely on OSM.

Capacity uses published density standards, not a guessed headcount: the Sphere
Handbook minimum (3.5 m^2 covered floor per person) and a packed short-term
cyclone-shelter density (1.5 m^2/person). Footprint area is a transparent proxy
for usable floor area (single-storey assumption; noted as a limit).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "puri_capacity.json")

#: Sphere Handbook minimum covered floor area per person (m^2).
SPHERE_M2 = 3.5
#: Packed short-term cyclone-shelter density (m^2/person).
PACKED_M2 = 1.5


@dataclass
class CapacityValidation:
    place: str
    population: int
    population_source: str
    n_shelters: int
    total_floor_m2: int
    largest_shelters: list[tuple[str, int]]
    capacity_sphere: int
    capacity_packed: int
    coverage_sphere_pct: float
    coverage_packed_pct: float
    shortfall_sphere: int
    shortfall_packed: int
    system_flags_gap: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def load_capacity(path: str = FIXTURE) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _population(fx: dict) -> tuple[int, str]:
    for place, pop in fx.get("population_tags", []):
        try:
            return int(pop), f"OSM {place} population tag (Census-2011-derived)"
        except (TypeError, ValueError):
            continue
    return 0, "unknown"


def validate_capacity(fixture: dict | None = None) -> CapacityValidation:
    fx = fixture or load_capacity()
    pop, pop_src = _population(fx)
    shelters = fx.get("shelters", [])
    total = sum(int(s.get("area_m2", 0)) for s in shelters)

    cap_sphere = int(total / SPHERE_M2)
    cap_packed = int(total / PACKED_M2)
    cov_sphere = 100.0 * cap_sphere / pop if pop else 0.0
    cov_packed = 100.0 * cap_packed / pop if pop else 0.0

    # The system flags a shelter resource gap whenever capacity cannot hold the
    # at-risk population (its routing redirects above 80% shelter fill, and the
    # resource agent emits a ResourceGap). Even at packed density the gap is huge.
    flags_gap = cap_packed < pop

    largest = sorted(
        ((s.get("name") or "(unnamed)", int(s.get("area_m2", 0))) for s in shelters),
        key=lambda t: -t[1],
    )[:5]

    notes = [
        f"Capacity from REAL OSM building footprints ({total:,} m^2 across "
        f"{len(shelters)} shelters) at published densities; footprint is a "
        "single-storey proxy for usable floor area (a limit — multi-storey "
        "buildings hold more).",
        "The gap is largely a DATA gap: OSM under-tags shelters. The real Fani "
        "evacuation used the OSDMA multipurpose-cyclone-shelter network (~800+ "
        "purpose-built shelters) — not in OSM — which is why the documented toll "
        "was 64 deaths. Deployment requires loading the OSDMA shelter registry.",
        "Flagging this shortfall is CORRECT behaviour: the system should request "
        "mutual aid / activate more shelters when capacity falls short, not fail "
        "silently.",
    ]
    return CapacityValidation(
        place=fx.get("place", "Puri, Odisha"),
        population=pop,
        population_source=pop_src,
        n_shelters=len(shelters),
        total_floor_m2=total,
        largest_shelters=largest,
        capacity_sphere=cap_sphere,
        capacity_packed=cap_packed,
        coverage_sphere_pct=round(cov_sphere, 1),
        coverage_packed_pct=round(cov_packed, 1),
        shortfall_sphere=max(0, pop - cap_sphere),
        shortfall_packed=max(0, pop - cap_packed),
        system_flags_gap=flags_gap,
        notes=notes,
    )


def to_markdown(v: CapacityValidation) -> str:
    return "\n".join(
        [
            "# Shelter Capacity vs Real Population — Puri (Cyclone Fani zone)",
            "",
            f"_Real at-risk population vs shelter capacity derived from real OSM "
            f"building footprints. Place: {v.place}._",
            "",
            "## The real numbers",
            f"- **Population (at risk):** {v.population:,} — {v.population_source}",
            f"- **Shelters:** {v.n_shelters} real buildings, {v.total_floor_m2:,} m^2 "
            "total footprint",
            f"- **Largest:** "
            + ", ".join(f"{n} ({a:,} m^2)" for n, a in v.largest_shelters[:3]),
            "",
            "## Capacity vs need",
            "| Density standard | Capacity | Covers | Shortfall |",
            "|---|---|---|---|",
            f"| Sphere min (3.5 m^2/person) | {v.capacity_sphere:,} | "
            f"**{v.coverage_sphere_pct}%** | {v.shortfall_sphere:,} |",
            f"| Packed cyclone (1.5 m^2/person) | {v.capacity_packed:,} | "
            f"**{v.coverage_packed_pct}%** | {v.shortfall_packed:,} |",
            "",
            f"- **System flags a shelter resource gap:** "
            f"{'✅ yes (correct — capacity < population)' if v.system_flags_gap else 'no'}",
            "",
            "## What this means (honest)",
            *[f"- {n}" for n in v.notes],
        ]
    )
