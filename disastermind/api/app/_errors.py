"""Error-classification helper for the dashboard's exception handlers."""
from json import JSONDecodeError
from typing import Any


def _is_json_decode(exc: Any) -> bool:
    """True if a validation error was actually caused by unparseable JSON.

    FastAPI surfaces a malformed request body as a :class:`RequestValidationError`
    whose underlying cause is a :class:`json.JSONDecodeError`. We sniff both the
    direct cause chain and the per-error ``type``/``msg`` so we can return a clear
    400 ``invalid_json`` instead of an opaque 422 schema error.
    """
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, JSONDecodeError):
        return True
    errors = getattr(exc, "errors", None)
    try:
        rows = errors() if callable(errors) else []
    except Exception:  # pragma: no cover - defensive
        return False
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("type") in ("json_invalid", "value_error.jsondecode"):
            return True
        ctx_err = (row.get("ctx") or {}).get("error")
        if isinstance(ctx_err, JSONDecodeError):
            return True
    return False
