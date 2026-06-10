"""Elasticsearch query-DSL builders for the audit index (PRD Step 9).

The decision/prediction audit trail (:class:`disastermind.audit.decision_log`)
is mirrored into a searchable Elasticsearch index by
:class:`disastermind.storage.elasticsearch_audit_repo.ElasticsearchAuditRepo`.
That repo embeds its query body inline; this module factors the **pure query-DSL
construction** out into dependency-free builders that emit the exact ``bool``
``must`` shape the repo's live path uses (``query_string`` for free text,
``term`` for field equality, ``range`` for inclusive ISO-8601 time windows).

There is NO ``elasticsearch`` import and NO network anywhere in this module — the
builders return plain dicts, so they are fully testable with no server (PRD Step
10 graceful degradation). The optional client is only touched by the live helper
in :mod:`disastermind.integrations.health`, lazily.
"""
from __future__ import annotations

from typing import Any

# Default audit index name — matches ElasticsearchAuditRepo's default.
AUDIT_INDEX = "disastermind-audit"

# Default timestamp field on audit docs (Message.to_dict() -> "timestamp").
DEFAULT_TS_FIELD = "timestamp"


def _enum_value(obj: Any) -> Any:
    """Return ``obj.value`` for enum members, else ``obj`` (accepts plain values)."""
    return getattr(obj, "value", obj)


def match_clause(field: str, text: Any) -> dict[str, Any]:
    """Full-text ``match`` clause on a single field."""
    return {"match": {field: _enum_value(text)}}


def query_string_clause(text: str, *, fields: list[str] | None = None) -> dict[str, Any]:
    """Free-text ``query_string`` clause (searches all fields by default)."""
    qs: dict[str, Any] = {"query": text}
    if fields:
        qs["fields"] = list(fields)
    return {"query_string": qs}


def term_clause(field: str, value: Any) -> dict[str, Any]:
    """Exact-equality ``term`` clause (accepts enum members)."""
    return {"term": {field: _enum_value(value)}}


def terms_clause(field: str, values: list[Any]) -> dict[str, Any]:
    """Set-membership ``terms`` clause."""
    return {"terms": {field: [_enum_value(v) for v in values]}}


def range_clause(
    field: str,
    *,
    gte: Any | None = None,
    lte: Any | None = None,
    gt: Any | None = None,
    lt: Any | None = None,
) -> dict[str, Any]:
    """Inclusive/exclusive ``range`` clause on a field (e.g. an ISO-8601 ts)."""
    rng: dict[str, Any] = {}
    if gte is not None:
        rng["gte"] = gte
    if lte is not None:
        rng["lte"] = lte
    if gt is not None:
        rng["gt"] = gt
    if lt is not None:
        rng["lt"] = lt
    if not rng:
        raise ValueError("range_clause requires at least one bound")
    return {"range": {field: rng}}


def bool_query(
    must: list[dict[str, Any]] | None = None,
    *,
    should: list[dict[str, Any]] | None = None,
    must_not: list[dict[str, Any]] | None = None,
    filter: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a ``bool`` query from clause lists; empty -> ``match_all``."""
    inner: dict[str, Any] = {}
    if must:
        inner["must"] = must
    if should:
        inner["should"] = should
    if must_not:
        inner["must_not"] = must_not
    if filter:
        inner["filter"] = filter
    if not inner:
        return {"match_all": {}}
    return {"bool": inner}


def audit_search_body(
    text: str | None = None,
    *,
    fields: dict[str, Any] | None = None,
    start: str | None = None,
    end: str | None = None,
    ts_field: str = DEFAULT_TS_FIELD,
    size: int = 50,
    sort_desc: bool = True,
) -> dict[str, Any]:
    """Build the full ``search`` body for the audit index.

    Mirrors :meth:`ElasticsearchAuditRepo._search_pg` exactly:
      * ``text``   -> a ``query_string`` ``must`` clause,
      * ``fields`` -> one ``term`` ``must`` clause per field (enum-aware),
      * ``start``/``end`` -> an inclusive ``range`` ``must`` clause on ``ts_field``.

    Empty inputs yield a ``match_all`` query. Results are sorted by ``ts_field``
    (descending by default — newest audit records first).
    """
    must: list[dict[str, Any]] = []
    if text:
        must.append(query_string_clause(text))
    for key, value in (fields or {}).items():
        must.append(term_clause(key, value))
    if start is not None or end is not None:
        must.append(range_clause(ts_field, gte=start, lte=end))
    body: dict[str, Any] = {"query": bool_query(must), "size": int(size)}
    body["sort"] = [{ts_field: {"order": "desc" if sort_desc else "asc"}}]
    return body


def audit_index_mapping() -> dict[str, Any]:
    """Index mapping for audit docs (keyword exact-match + date ts).

    Used to create the index with field types that make ``term``/``range``
    queries above behave (keyword for ids/types, date for the timestamp).
    """
    return {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "sender": {"type": "keyword"},
                "recipient": {"type": "keyword"},
                "type": {"type": "keyword"},
                "priority": {"type": "integer"},
                "module": {"type": "keyword"},
                "incident_id": {"type": "keyword"},
                "topic": {"type": "keyword"},
                "escalation_trigger": {"type": "keyword"},
                DEFAULT_TS_FIELD: {"type": "date"},
                "reasoning": {"type": "text"},
                "payload": {"type": "object", "enabled": True},
            }
        }
    }
