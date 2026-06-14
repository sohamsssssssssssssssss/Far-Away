"""Backward-compatible pagination for the dashboard's list views.

When BOTH ``limit`` and ``offset`` are ``None`` the legacy bare-list shape is
returned unchanged; supplying either query param switches to the
``{"items", "total", "limit", "offset"}`` envelope.
"""
from typing import Any

from ._constants import _DEFAULT_PAGE_LIMIT


def _paginate(
    rows: list[dict[str, Any]],
    limit: int | None,
    offset: int | None,
) -> Any:
    """Return ``rows`` either as a bare list (legacy) or a paginated envelope.

    Backward compatible: when BOTH ``limit`` and ``offset`` are ``None`` the
    caller asked for the legacy shape and we return the list unchanged. As soon
    as either query parameter is supplied we return
    ``{"items", "total", "limit", "offset"}`` over the full ``rows`` set.
    """
    if limit is None and offset is None:
        return rows
    total = len(rows)
    off = max(0, int(offset)) if offset is not None else 0
    lim = _DEFAULT_PAGE_LIMIT if limit is None else max(0, int(limit))
    window = rows[off : off + lim]
    return {"items": window, "total": total, "limit": lim, "offset": off}
