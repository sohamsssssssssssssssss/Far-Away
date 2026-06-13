"""Locate the durable persistence facade behind a built coordination loop.

The history endpoints prefer a durable store; this helper finds the
``persistence.state`` agent's ``Storage`` facade, or ``None`` when no persistor
is wired (the routes then fall back to the in-memory service).
"""
from typing import Any


def _find_persisted_storage(loop: Any) -> Any:
    """Return the StatePersistor's ``Storage`` facade if a persistor is wired.

    The history endpoints prefer a durable store: we locate the ``persistence.state``
    agent on ``loop.agents`` (the :class:`~disastermind.persistence.persistor.StatePersistor`)
    and hand back its ``.storage``. Returns ``None`` when no loop/persistor exists,
    in which case the routes fall back to the in-memory :class:`DashboardService`.
    """
    for agent in list(getattr(loop, "agents", []) or []):
        if getattr(agent, "name", "") == "persistence.state":
            storage = getattr(agent, "storage", None)
            if storage is not None:
                return storage
    return None
