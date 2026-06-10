"""Resource-allocation optimisation core (PRD Step 4).

Pure, agent-agnostic optimisation helpers. The :class:`ResourceAllocationAgent`
feeds these the current asset inventory, the prediction risk-cells and a
vulnerability map; they return an asset->cell assignment that

  * maximises *weighted* population covered,
  * minimises response time (haversine distance / asset speed),
  * honours the EQUITY CONSTRAINT — elderly density, hospital proximity, road
    accessibility and informal-settlement density are weighted *equally* with
    raw urban population via
    :meth:`~disastermind.models.domain.VulnerabilityProfile.weight`.

Two solvers are offered behind one interface (PRD Step 10, graceful
degradation):

  * :func:`solve_lp`     — Mixed-Integer LP via **PuLP** (lazy import).
  * :func:`solve_greedy` — deterministic weighted greedy assignment in pure
    stdlib; the guaranteed fallback when PuLP / a solver backend is absent.

:func:`optimise` picks LP when available and silently degrades to greedy.

No heavy library is imported at module load; everything optional is imported
lazily inside the function that needs it and wrapped in ``try/except``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...models.domain import AssetType, VulnerabilityProfile
from ...models.geo import LatLon, haversine

# Nominal cruise speeds (km/h) used to convert distance -> response-time minutes.
# Deliberately conservative; only relative ordering matters for the objective.
_ASSET_SPEED_KMH: dict[str, float] = {
    AssetType.BOAT.value: 35.0,
    AssetType.HELICOPTER.value: 200.0,
    AssetType.NDRF_TEAM.value: 50.0,
    AssetType.SDRF_TEAM.value: 50.0,
    AssetType.MEDICAL_UNIT.value: 60.0,
    AssetType.FIRE_ENGINE.value: 55.0,
    AssetType.USAR_TEAM.value: 45.0,
}
_DEFAULT_SPEED_KMH = 50.0

# Asset types that are pre-positioned and may be auto-deployed within this radius
# without escalation (PRD Step 7 autonomous-decision boundary).
PREPOSITION_RADIUS_KM = 50.0


# --------------------------------------------------------------------------- inputs
@dataclass
class DemandCell:
    """A unit of demand the optimiser must try to cover.

    Built from a PREDICTION ``risk_cell`` joined with its vulnerability profile.
    ``weight`` already folds in the equity multiplier so the objective treats a
    vulnerable cell of N people like an urban cell of ``N * weight`` people.
    """

    cell_id: str
    centroid: LatLon
    population_at_risk: int
    probability: float
    horizon_minutes: int
    vulnerability: VulnerabilityProfile = field(default_factory=VulnerabilityProfile)
    needed_asset_types: tuple[str, ...] = ()

    @property
    def equity_weight(self) -> float:
        return self.vulnerability.weight()

    @property
    def demand_score(self) -> float:
        """Weighted population at risk (equity-adjusted, urgency-scaled)."""
        urgency = 0.5 + 0.5 * max(0.0, min(1.0, self.probability))
        return self.population_at_risk * self.equity_weight * urgency


@dataclass
class AssetView:
    """Lightweight asset record decoupled from the full domain dataclass."""

    asset_id: str
    type: str  # AssetType value
    location: LatLon
    capacity: int = 0
    fuel_pct: float = 100.0


@dataclass
class Assignment:
    asset_id: str
    asset_type: str
    cell_id: str
    eta_minutes: float
    covered: int  # raw population this asset can cover at the cell
    weighted_value: float
    distance_km: float
    within_preposition: bool


@dataclass
class OptimisationResult:
    assignments: list[Assignment]
    solver: str  # "pulp" | "greedy"
    objective: float
    # cell_id -> remaining (uncovered) raw population after assignment
    shortfall: dict[str, int] = field(default_factory=dict)


# ----------------------------------------------------------------------- utilities
def eta_minutes(asset_type: str, distance_km: float) -> float:
    speed = _ASSET_SPEED_KMH.get(asset_type, _DEFAULT_SPEED_KMH)
    if speed <= 0:
        speed = _DEFAULT_SPEED_KMH
    return (distance_km / speed) * 60.0


def distance_km(a: LatLon, b: LatLon) -> float:
    return haversine(a, b) / 1000.0


def _pair_value(asset: AssetView, cell: DemandCell, dist_km: float) -> float:
    """Marginal value of sending ``asset`` to ``cell``.

    Maximise weighted population covered while penalising response time. A
    closer asset and a higher equity weight both raise the score, so the
    objective jointly satisfies *coverage* and *speed*.
    """
    cap = asset.capacity if asset.capacity > 0 else 1
    covered = min(cap, cell.population_at_risk) if cell.population_at_risk else cap
    et = eta_minutes(asset.type, dist_km)
    time_penalty = 1.0 / (1.0 + et / 30.0)  # 1.0 at door-step, ->0 far away
    fuel_factor = max(0.1, asset.fuel_pct / 100.0)
    return covered * cell.equity_weight * time_penalty * fuel_factor


def _compatible(asset: AssetView, cell: DemandCell) -> bool:
    """True if this asset type is useful for this cell's stated needs.

    When a cell does not specify ``needed_asset_types`` every asset is eligible
    (the optimiser still ranks by value), so missing data never starves a cell.
    """
    if not cell.needed_asset_types:
        return True
    return asset.type in cell.needed_asset_types


# ----------------------------------------------------------------------- greedy
def solve_greedy(
    assets: list[AssetView], cells: list[DemandCell]
) -> OptimisationResult:
    """Deterministic weighted greedy assignment (pure stdlib fallback).

    Builds every compatible (asset, cell) pair, scores it, and greedily commits
    the highest-value pairs such that each asset is used once and a cell stops
    pulling more assets once its at-risk population is covered. Deterministic
    tie-breaking on (asset_id, cell_id) keeps the audit trail reproducible.
    """
    remaining: dict[str, int] = {
        c.cell_id: max(0, c.population_at_risk) for c in cells
    }
    cell_by_id = {c.cell_id: c for c in cells}

    pairs: list[tuple[float, str, str, float]] = []  # (value, asset_id, cell_id, dist)
    for a in assets:
        for c in cells:
            if not _compatible(a, c):
                continue
            d = distance_km(a.location, c.centroid)
            v = _pair_value(a, c, d)
            pairs.append((v, a.asset_id, c.cell_id, d))

    # Highest value first; deterministic tie-break.
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))

    asset_by_id = {a.asset_id: a for a in assets}
    used_assets: set[str] = set()
    assignments: list[Assignment] = []
    objective = 0.0

    for value, asset_id, cell_id, d in pairs:
        if asset_id in used_assets:
            continue
        if remaining.get(cell_id, 0) <= 0 and cell_by_id[cell_id].population_at_risk > 0:
            continue
        asset = asset_by_id[asset_id]
        cell = cell_by_id[cell_id]
        cap = asset.capacity if asset.capacity > 0 else max(1, remaining.get(cell_id, 1))
        covered = min(cap, remaining.get(cell_id, cap)) if cell.population_at_risk else cap
        et = eta_minutes(asset.type, d)
        assignments.append(
            Assignment(
                asset_id=asset_id,
                asset_type=asset.type,
                cell_id=cell_id,
                eta_minutes=round(et, 1),
                covered=int(covered),
                weighted_value=round(value, 4),
                distance_km=round(d, 3),
                within_preposition=d <= PREPOSITION_RADIUS_KM,
            )
        )
        used_assets.add(asset_id)
        if cell.population_at_risk > 0:
            remaining[cell_id] = max(0, remaining[cell_id] - int(covered))
        objective += value

    shortfall = {cid: rem for cid, rem in remaining.items() if rem > 0}
    return OptimisationResult(
        assignments=assignments,
        solver="greedy",
        objective=round(objective, 4),
        shortfall=shortfall,
    )


# --------------------------------------------------------------------------- LP
def solve_lp(assets: list[AssetView], cells: list[DemandCell]) -> OptimisationResult:
    """Mixed-Integer LP assignment via PuLP (lazy import).

    Decision variables x[a,c] in {0,1}: asset ``a`` assigned to cell ``c``.
    Maximise total weighted coverage value subject to each asset used at most
    once. Raises if PuLP / a solver is unavailable so the caller can fall back.
    """
    import pulp  # lazy — never imported at module load (PRD Step 3 rule)

    prob = pulp.LpProblem("disastermind_resource_allocation", pulp.LpMaximize)

    cell_by_id = {c.cell_id: c for c in cells}
    asset_by_id = {a.asset_id: a for a in assets}

    x: dict[tuple[str, str], "pulp.LpVariable"] = {}
    value: dict[tuple[str, str], float] = {}
    dist: dict[tuple[str, str], float] = {}
    for a in assets:
        for c in cells:
            if not _compatible(a, c):
                continue
            d = distance_km(a.location, c.centroid)
            key = (a.asset_id, c.cell_id)
            dist[key] = d
            value[key] = _pair_value(a, c, d)
            x[key] = pulp.LpVariable(f"x_{a.asset_id}_{c.cell_id}", cat="Binary")

    # Objective: maximise weighted coverage value.
    prob += pulp.lpSum(value[k] * x[k] for k in x)

    # Each asset assigned to at most one cell.
    for a in assets:
        a_vars = [x[k] for k in x if k[0] == a.asset_id]
        if a_vars:
            prob += pulp.lpSum(a_vars) <= 1, f"asset_once_{a.asset_id}"

    # Solve quietly; suppress solver chatter.
    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    if status not in ("Optimal", "Feasible"):
        raise RuntimeError(f"LP solver returned status={status}")

    remaining: dict[str, int] = {
        c.cell_id: max(0, c.population_at_risk) for c in cells
    }
    assignments: list[Assignment] = []
    objective = 0.0
    for key, var in x.items():
        if var.value() is None or var.value() < 0.5:
            continue
        asset_id, cell_id = key
        asset = asset_by_id[asset_id]
        cell = cell_by_id[cell_id]
        d = dist[key]
        cap = asset.capacity if asset.capacity > 0 else max(1, remaining.get(cell_id, 1))
        covered = min(cap, remaining.get(cell_id, cap)) if cell.population_at_risk else cap
        et = eta_minutes(asset.type, d)
        assignments.append(
            Assignment(
                asset_id=asset_id,
                asset_type=asset.type,
                cell_id=cell_id,
                eta_minutes=round(et, 1),
                covered=int(covered),
                weighted_value=round(value[key], 4),
                distance_km=round(d, 3),
                within_preposition=d <= PREPOSITION_RADIUS_KM,
            )
        )
        if cell.population_at_risk > 0:
            remaining[cell_id] = max(0, remaining[cell_id] - int(covered))
        objective += value[key]

    shortfall = {cid: rem for cid, rem in remaining.items() if rem > 0}
    return OptimisationResult(
        assignments=assignments,
        solver="pulp",
        objective=round(objective, 4),
        shortfall=shortfall,
    )


def optimise(
    assets: list[AssetView], cells: list[DemandCell], prefer_lp: bool = True
) -> OptimisationResult:
    """Run the best available solver, degrading LP -> greedy on any failure.

    The package must import and the test-suite must run with stdlib only, so a
    missing PuLP (or missing CBC backend) is *expected* and silently handled.
    """
    if not assets or not cells:
        return OptimisationResult(
            assignments=[],
            solver="greedy",
            objective=0.0,
            shortfall={c.cell_id: max(0, c.population_at_risk) for c in cells},
        )
    if prefer_lp:
        try:
            return solve_lp(assets, cells)
        except Exception:  # ImportError, missing CBC, infeasible — degrade
            pass
    return solve_greedy(assets, cells)
