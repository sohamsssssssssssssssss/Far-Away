"""Shared HTTP transport for Tier 3 live feed fetches (PRD Step 2 / Step 10).

The live ``fetch()`` path of the free, no-key sources (USGS, Open-Meteo) needs a
real network GET, but DisasterMind must import and test with **stdlib only** and
never make a network call in a test (PRD Step 10, graceful degradation). This
module centralises that policy:

  * :func:`http_get_json` / :func:`http_get_text` prefer a lazily-imported
    ``httpx`` (better timeouts/HTTP-2) and fall back to stdlib
    :mod:`urllib.request` when ``httpx`` is absent — so there is **no hard
    third-party dependency** on the import or test path.
  * The transport is injectable: every caller passes ``transport=`` only in
    tests (a recorded-fixture stub), so production code uses the real network
    while tests stay fully offline and deterministic.

Nothing here is exercised by the test-suite via a real socket: tests either call
:func:`parse` directly on a committed fixture, or monkeypatch ``transport`` with
a callable that returns recorded bytes/JSON.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable
from urllib.request import Request, urlopen

log = logging.getLogger("disastermind.ingestion.http")

#: A transport is a callable ``(url, timeout) -> (status_code, text)``.
Transport = Callable[[str, float], "tuple[int, str]"]

#: Browser-ish UA — some public feeds 403 an empty UA.
_USER_AGENT = "DisasterMind/1.0 (+https://github.com/disastermind) edge-feed"


def _httpx_transport(url: str, timeout: float) -> tuple[int, str]:  # pragma: no cover - network
    """Fetch ``url`` with a lazily-imported ``httpx`` (preferred transport)."""
    import httpx  # type: ignore

    resp = httpx.get(url, timeout=timeout, headers={"User-Agent": _USER_AGENT})
    return resp.status_code, resp.text


def _urllib_transport(url: str, timeout: float) -> tuple[int, str]:  # pragma: no cover - network
    """Fetch ``url`` with stdlib :mod:`urllib.request` (no third-party dep)."""
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URLs only
        status = getattr(resp, "status", None) or resp.getcode() or 200
        charset = resp.headers.get_content_charset() or "utf-8"
        return int(status), resp.read().decode(charset, errors="replace")


def default_transport(url: str, timeout: float) -> tuple[int, str]:  # pragma: no cover - network
    """Return ``(status, text)`` using ``httpx`` if available, else ``urllib``.

    Never raises for a *missing* ``httpx`` — only network/HTTP errors propagate,
    and callers degrade to their ``sample()`` fixture on any exception.
    """
    try:
        import httpx  # type: ignore  # noqa: F401

        return _httpx_transport(url, timeout)
    except ImportError:
        return _urllib_transport(url, timeout)


def http_get_text(
    url: str,
    timeout: float = 10.0,
    transport: Transport | None = None,
) -> str:
    """GET ``url`` and return the response body as text.

    Raises on a non-2xx status or transport error so the feed adapter can catch
    it and degrade to ``sample()``. ``transport`` is injected only by tests.
    """
    status, text = (transport or default_transport)(url, timeout)
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status} from {url}")
    return text


def http_get_json(
    url: str,
    timeout: float = 10.0,
    transport: Transport | None = None,
) -> Any:
    """GET ``url`` and decode the JSON body (see :func:`http_get_text`)."""
    return json.loads(http_get_text(url, timeout=timeout, transport=transport))
