"""Integration tests are collected ONLY when DM_INTEGRATION=1 (i.e. docker-compose is up).
This keeps the default `pytest -q` count unchanged (no added skips)."""
import os

collect_ignore_glob = [] if os.environ.get("DM_INTEGRATION") else ["test_*.py"]


def _reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    """Quick TCP connect check so a guarded test self-skips when its service is down."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
