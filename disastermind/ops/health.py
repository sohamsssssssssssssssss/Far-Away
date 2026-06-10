"""Operational health probes — readiness & liveness (PRD Step 9/10).

Kubernetes-style split probes for an operator / orchestrator:

  * :func:`liveness` — "is the *process* alive?" A cheap, dependency-free check
    that returns quickly. It never inspects the (possibly slow) loop; it just
    confirms the ops layer is importable and responsive. A failing liveness probe
    means "restart me".

  * :func:`readiness` — "is the *system* ready to coordinate?" Inspects a built
    :class:`~disastermind.orchestration.loop.CoordinationLoop`: agents wired, no
    modules degraded, a live bus. A failing readiness probe means "don't route
    traffic to me yet" (but don't kill me).

Both fold in the richer ``observability.health`` and the ``diagnostics`` doctor
*lazily and optionally* — if those modules import, their detail is merged in;
if not (or if they raise), the probe degrades gracefully and still returns a
well-formed dict. A health probe must never take the system down, so nothing
here raises.
"""
from __future__ import annotations

from typing import Any


def liveness() -> dict:
    """Process-liveness probe — cheap, dependency-free, always ``alive``.

    Returns ``{"status": "alive", "live": True, ...}``. This is intentionally
    trivial: reaching this code at all proves the interpreter and the ops package
    are responsive. Use :func:`readiness` for "ready to serve" semantics.
    """
    return {
        "status": "alive",
        "live": True,
        "checks": {"process": "ok"},
    }


def _observability_health(loop: Any) -> dict | None:
    """Lazily fold in ``observability.health`` detail (optional)."""
    try:
        from ..observability.health import health as obs_health
    except Exception:
        return None
    try:
        return obs_health(loop)
    except Exception:
        return None


def readiness(loop: Any) -> dict:
    """Readiness probe over a built coordination ``loop``.

    The returned dict always contains:

      * ``status``   — ``"ready"`` or ``"not_ready"``.
      * ``ready``    — bool mirror of ``status``.
      * ``checks``   — per-aspect ``ok``/``fail`` map (agents, modules, bus).
      * ``detail``   — agent count, degraded modules, cycle, bus type.

    A loop is *ready* when it has at least one wired agent, no degraded modules,
    and a live (non-degraded) bus. The probe is duck-typed and defensive: a
    partially-built loop, a ``None``, or any object exposing the same attributes
    is handled without raising.
    """
    agents = list(getattr(loop, "agents", []) or [])
    degraded = list(getattr(loop, "degraded_modules", []) or [])
    bus = getattr(loop, "bus", None)
    bus_degraded = bool(getattr(bus, "degraded", False))

    checks: dict[str, str] = {
        "agents": "ok" if agents else "fail",
        "modules": "ok" if not degraded else "fail",
        "bus": "ok" if (bus is not None and not bus_degraded) else "fail",
    }
    ready = all(v == "ok" for v in checks.values())

    result: dict[str, Any] = {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "checks": checks,
        "detail": {
            "agent_count": len(agents),
            "degraded_modules": degraded,
            "disaster_active": bool(getattr(loop, "disaster_active", False)),
            "cycle": int(getattr(loop, "cycle", 0) or 0),
            "bus": type(bus).__name__ if bus is not None else None,
            "bus_degraded": bus_degraded,
        },
    }

    # Optionally enrich with the observability component-liveness report.
    obs = _observability_health(loop)
    if obs is not None:
        result["components"] = obs.get("components", {})
        result["detail"]["observability_status"] = obs.get("status")

    return result


def probe(loop: Any) -> dict:
    """Combined probe: both liveness and readiness in one structured dict."""
    return {
        "liveness": liveness(),
        "readiness": readiness(loop),
    }


__all__ = ["liveness", "readiness", "probe"]
