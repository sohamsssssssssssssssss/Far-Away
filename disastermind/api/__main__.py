"""``python -m disastermind.api`` — serve the live Commander Dashboard.

PRD Step 7 (dashboard) + Step 10 (WebSocket refresh). This thin CLI builds a full
DisasterMind system via :func:`disastermind.api.server.create_server` and serves
the FastAPI app (and static UI) with uvicorn. FastAPI/uvicorn are imported lazily
inside :meth:`DashboardServer.run`, so importing this module never requires them
(HARD RULE 2). Host/port come from ``DM_API_HOST`` / ``DM_API_PORT`` or
``--host`` / ``--port``.

Examples
--------
    python -m disastermind.api
    python -m disastermind.api --host 0.0.0.0 --port 9001
"""
from __future__ import annotations

import argparse
import os
import sys

from .server import create_server


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="disastermind.api",
        description="Serve the DisasterMind Commander Dashboard (PRD Step 7 + 10).",
    )
    # Hosted platforms (Railway/Heroku/Fly) inject $PORT and expect the process to
    # bind 0.0.0.0 so their router can reach it; locally we stay on 127.0.0.1.
    _hosted = bool(os.environ.get("PORT"))
    parser.add_argument(
        "--host",
        default=os.environ.get("DM_API_HOST") or ("0.0.0.0" if _hosted else "127.0.0.1"),
        help="Bind address (default: %(default)s; env DM_API_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT") or os.environ.get("DM_API_PORT") or "8000"),
        help="Bind port (default: %(default)s; env PORT / DM_API_PORT).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build + serve the dashboard; return a process exit code."""
    args = _parse_args(argv)
    server = create_server()
    try:
        server.run(host=args.host, port=args.port)
    except RuntimeError as exc:  # FastAPI/uvicorn missing -> fail loudly, exit 1
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive Ctrl-C
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI
    raise SystemExit(main())
