"""Live Elasticsearch index + search round-trip (docker-compose `elasticsearch`).

Gated by tests/integration/conftest.py (collected only when DM_INTEGRATION=1).
Self-skips when the elasticsearch client is missing or ES is unreachable. Indexes
audit records (`Message`) into a unique per-run index and asserts they become
searchable (accounting for ES near-real-time refresh).
"""
from __future__ import annotations

import socket
import time
import uuid

import pytest

pytest.importorskip("elasticsearch")

from disastermind.core.contracts import Message, MessageType, Priority, Topic  # noqa: E402
from disastermind.storage.elasticsearch_audit_repo import ElasticsearchAuditRepo  # noqa: E402

ES_HOST = "localhost"
ES_PORT = 9200
ES_URL = f"http://{ES_HOST}:{ES_PORT}"


def _reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(ES_HOST, ES_PORT),
    reason=f"Elasticsearch unreachable at {ES_URL} (start `docker compose up -d elasticsearch`)",
)


def _messages(incident_id: str, n: int = 3) -> list[Message]:
    return [
        Message(
            sender="tier1.commander",
            recipient="tier3.dispatch",
            type=MessageType.INSTRUCTION,
            priority=Priority.HIGH,
            payload={"kind": "dispatch", "order": {"site": f"zone-{i}"}},
            reasoning=[f"audit record {i}"],
            topic=Topic.DISPATCH,
            incident_id=incident_id,
        )
        for i in range(n)
    ]


def test_elasticsearch_index_and_search():
    tag = uuid.uuid4().hex[:10]
    index = f"dm-it-audit-{tag}"
    repo = ElasticsearchAuditRepo(url=ES_URL, index=index)
    if repo.is_fallback:
        pytest.skip("elasticsearch client present but ES connection failed")

    records = _messages(incident_id=tag, n=3)
    want_ids = {m.id for m in records}
    assert repo.index_many(records) == 3

    # ES is near-real-time: poll count until the refresh makes docs searchable.
    deadline = time.time() + 20.0
    while time.time() < deadline and repo.count() < 3:
        time.sleep(1.0)
    assert repo.count() >= 3, "indexed docs did not become countable within 20s"

    # A match-all search returns our indexed audit records.
    hits = repo.search(size=50)
    got_ids = {h.get("id") for h in hits}
    assert want_ids <= got_ids
    # Every hit carries the canonical Message audit shape.
    assert all("topic" in h and "timestamp" in h for h in hits)
