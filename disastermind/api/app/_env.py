"""Small, defensive environment-variable readers used by the route factory.

Both helpers fall back to the supplied default on *any* parse error and reject
non-positive values, so a malformed override can never weaken a guard rail.
"""
import os


def _env_int(key: str, default: int) -> int:
    """Read a positive integer env var, falling back to ``default`` on any error."""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _env_float(key: str, default: float) -> float:
    """Read a positive float env var, falling back to ``default`` on any error."""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw.strip())
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default
