"""Component liveness probe (PRD Step 10 graceful degradation).

:func:`health` inspects a built :class:`~disastermind.orchestration.loop.CoordinationLoop`
and reports the operational status of the system: which modules failed to load
(``loop.degraded_modules``), the live agent count, whether a disaster is active,
the current cycle, and the bus type. The probe is duck-typed and defensive so it
works against the real loop, a partially-built loop, or any object exposing the
same attributes — and never raises (a health check must not take the system
down). It is the backend of the ``/healthz`` endpoint behind PRD Step 9/10.
"""
from __future__ import annotations

from typing import Any


def health(loop: Any) -> dict:
    """Return a component-liveness report for ``loop``.

    The returned dict always contains a top-level ``status`` of ``"ok"`` (no
    modules degraded) or ``"degraded"`` (one or more modules failed to load),
    plus enough detail for an operator dashboard.
    """
    agents = list(getattr(loop, "agents", []) or [])
    degraded = list(getattr(loop, "degraded_modules", []) or [])

    components: dict[str, dict[str, Any]] = {}
    for a in agents:
        name = getattr(a, "name", repr(a))
        components[name] = {
            "status": "up",
            "tier": int(getattr(a, "tier", 0)) or None,
            "decision_authority": bool(getattr(a, "decision_authority", False)),
            "subscriptions": list(getattr(a, "subscriptions", []) or []),
        }

    bus = getattr(loop, "bus", None)
    bus_status = {
        "type": type(bus).__name__ if bus is not None else None,
        # KafkaBus exposes a ``degraded`` flag; absence means in-memory (healthy).
        "degraded": bool(getattr(bus, "degraded", False)),
    }

    return {
        "status": "degraded" if degraded else "ok",
        "agent_count": len(agents),
        "degraded_modules": degraded,
        "disaster_active": bool(getattr(loop, "disaster_active", False)),
        "cycle": int(getattr(loop, "cycle", 0) or 0),
        "bus": bus_status,
        "components": components,
    }
