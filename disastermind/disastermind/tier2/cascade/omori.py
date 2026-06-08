"""Pure-Python Omori-Utsu aftershock model (PRD Step 3, Module B).

The modified Omori law (Utsu, 1961) describes the temporal decay of aftershock
rate following a mainshock::

    n(t) = K / (t + c) ** p

where ``t`` is time since the mainshock (days), and ``K``, ``c``, ``p`` are
parameters. ``K`` scales with mainshock magnitude (more energy => more
aftershocks); ``c`` is a short offset (hours) and ``p`` (~0.9-1.4) governs the
decay rate.

The expected *number* of aftershocks (of any magnitude >= the cutoff used to
fit) in a window ``[t1, t2]`` is the analytic integral of ``n(t)``::

    N(t1, t2) = K * ((t2 + c)**(1-p) - (t1 + c)**(1-p)) / (1 - p)     (p != 1)
              = K * (ln(t2 + c) - ln(t1 + c))                          (p == 1)

To turn that into a probability of at least one M>=M0 aftershock we combine the
Omori rate with the Gutenberg-Richter frequency-magnitude relation::

    log10 N(>=M) = a - b * M

so the fraction of aftershocks at or above ``M0`` (relative to the completeness
magnitude ``Mc`` the K was fit to) is ``10 ** (-b * (M0 - Mc))``. Treating
events as a (non-homogeneous) Poisson process, the probability of one or more is
``1 - exp(-lambda)``.

This module is deliberately stdlib-only (``math`` only): the seismology here is
analytic, so there is no optional-library fast path to fall back from. Defaults
follow widely used Reasenberg-Jones / USGS aftershock-forecasting conventions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

HOURS_PER_DAY = 24.0


@dataclass(frozen=True)
class OmoriParams:
    """Fitted modified-Omori + Gutenberg-Richter parameters.

    Attributes:
        k: productivity (events/day at t≈0); scales with mainshock magnitude.
        c: time offset in days (early-time roll-off), typically ~0.05 d.
        p: decay exponent, typically ~1.1.
        a: Gutenberg-Richter productivity intercept (Reasenberg-Jones style).
        b: Gutenberg-Richter slope, typically ~1.0.
        mc: magnitude of completeness the rate was fit to.
        mainshock_magnitude: source magnitude (for provenance / SHAP).
    """

    k: float
    c: float
    p: float
    a: float
    b: float
    mc: float
    mainshock_magnitude: float


def fit_params(
    magnitude: float,
    depth_km: float = 10.0,
    *,
    b: float = 1.0,
    p: float = 1.1,
    c: float = 0.05,
) -> OmoriParams:
    """Derive Omori-Utsu parameters from a mainshock magnitude/depth.

    No regression data is available in this offline module, so we use the
    Reasenberg & Jones (1989) generic-California parameterisation, which makes
    productivity a function of mainshock magnitude::

        a' = a + b * (M_main - 1)              (productivity intercept)
        K  = 10 ** a'                          (events/day scale)

    with generic ``a = -1.67`` (we keep the constant explicit). Depth modestly
    suppresses surface-felt aftershock productivity: deeper ruptures (>70 km)
    relax the energy more diffusely, so we apply a small geometric damping.
    All of this is a transparent heuristic — PRD Step 9 wants explainable, not
    black-box, forecasts (PRD Step 3 Module B).
    """
    a_generic = -1.67
    # Completeness: assume we can catalogue down to roughly M3 for a regional net.
    mc = 3.0
    a_prod = a_generic + b * (magnitude - 1.0)
    k = 10.0 ** a_prod
    # Depth damping: shallow (<= 35 km) crustal events are fully productive;
    # deeper events progressively less so (clamped to a sensible floor).
    depth_factor = 1.0
    if depth_km > 35.0:
        depth_factor = max(0.4, 1.0 - (depth_km - 35.0) / 200.0)
    k *= depth_factor
    return OmoriParams(
        k=k, c=c, p=p, a=a_prod, b=b, mc=mc, mainshock_magnitude=magnitude
    )


def expected_count(params: OmoriParams, t1_days: float, t2_days: float) -> float:
    """Expected number of M>=Mc aftershocks in the window ``[t1, t2]`` (days).

    Analytic integral of the modified Omori rate ``K/(t+c)**p``.
    """
    if t2_days <= t1_days:
        return 0.0
    k, c, p = params.k, params.c, params.p
    if abs(p - 1.0) < 1e-9:
        return k * (math.log(t2_days + c) - math.log(t1_days + c))
    return k * ((t2_days + c) ** (1.0 - p) - (t1_days + c) ** (1.0 - p)) / (1.0 - p)


def expected_count_above(
    params: OmoriParams, m0: float, t1_days: float, t2_days: float
) -> float:
    """Expected number of aftershocks with magnitude >= ``m0`` in ``[t1, t2]``.

    Scales the Omori count by the Gutenberg-Richter fraction at/above ``m0``.
    """
    base = expected_count(params, t1_days, t2_days)
    gr_fraction = 10.0 ** (-params.b * (m0 - params.mc))
    return base * gr_fraction


def probability_at_least_one(
    params: OmoriParams, m0: float, t1_days: float, t2_days: float
) -> float:
    """Poisson probability of >=1 aftershock with M>=``m0`` in ``[t1, t2]``."""
    lam = expected_count_above(params, m0, t1_days, t2_days)
    return 1.0 - math.exp(-lam)


def probability_by_horizon(
    params: OmoriParams,
    m0: float = 5.0,
    horizons_hours: tuple[int, ...] = (24, 48, 72),
    *,
    start_hours: float = 0.0,
) -> dict[int, float]:
    """P(>=1 M>=m0 aftershock) cumulatively from ``start`` to each horizon.

    Returns a mapping ``{24: p24, 48: p48, 72: p72}`` (PRD Step 3 Module B:
    "M5.0+ aftershock probability at 24/48/72h").
    """
    t1 = start_hours / HOURS_PER_DAY
    out: dict[int, float] = {}
    for h in horizons_hours:
        out[h] = probability_at_least_one(params, m0, t1, h / HOURS_PER_DAY)
    return out
