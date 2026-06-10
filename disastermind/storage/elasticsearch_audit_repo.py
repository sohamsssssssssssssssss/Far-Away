"""Audit-record index/search repository (PRD Step 9 — Elasticsearch).

Complements the tamper-evident JSONL hash-chain in
:class:`disastermind.audit.decision_log.DecisionLogger` with a *searchable*
full audit trail: index decision/prediction records and query them by free
text, field equality, or time range (incident review, after-action analysis).

Backend selection (see :class:`~disastermind.storage.facade.Storage`):
  * **Elasticsearch** when a URL is configured — the ``elasticsearch`` client
    is imported *lazily* inside :meth:`_connect`, wrapped in try/except.
  * **Fallback** an in-memory list with a tiny stdlib query engine (case-
    insensitive substring match + field filters + ts range), so audit search
    works fully offline (PRD Step 10). No network at import or in any test.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from ._common import to_jsonable

log = logging.getLogger("disastermind.storage.elasticsearch")


class ElasticsearchAuditRepo:
    """Index + search audit records.

    Pass a non-empty ``url`` to attempt a connection; on any failure the repo
    degrades to the in-memory fallback search engine.
    """

    def __init__(self, url: str = "", index: str = "disastermind-audit") -> None:
        self.url = url
        self.index = index
        self._docs: list[dict[str, Any]] = []
        self._es = self._connect(url) if url else None

    @property
    def is_fallback(self) -> bool:
        return self._es is None

    def _connect(self, url: str):  # pragma: no cover - optional dependency/network
        try:
            from elasticsearch import Elasticsearch  # type: ignore

            return Elasticsearch(url)
        except Exception:
            log.warning("elasticsearch unavailable; in-memory audit search fallback")
            return None

    # --------------------------------------------------------------------- index
    def index_record(self, record: Any) -> dict[str, Any]:
        """Index one audit record (a :class:`Message`, dataclass, or dict)."""
        doc = self._as_doc(record)
        if self._es is None:
            self._docs.append(doc)
            return doc
        return self._index_pg(doc)  # pragma: no cover

    def index_many(self, records: Iterable[Any]) -> int:
        n = 0
        for r in records:
            self.index_record(r)
            n += 1
        return n

    @staticmethod
    def _as_doc(record: Any) -> dict[str, Any]:
        # Message exposes to_dict(); other dataclasses/dicts go through to_jsonable.
        to_dict = getattr(record, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        result = to_jsonable(record)
        if not isinstance(result, dict):
            raise TypeError(f"audit record must serialise to a dict, got {type(record)!r}")
        return result

    # -------------------------------------------------------------------- search
    def search(
        self,
        text: str | None = None,
        *,
        fields: dict[str, Any] | None = None,
        start: str | None = None,
        end: str | None = None,
        ts_field: str = "timestamp",
        size: int = 50,
    ) -> list[dict[str, Any]]:
        """Return matching docs.

        * ``text`` — case-insensitive substring match anywhere in the doc.
        * ``fields`` — exact equality on top-level fields (e.g. ``{"sender": ..}``).
        * ``start``/``end`` — inclusive ISO-8601 range on ``ts_field``.
        """
        if self._es is not None:
            return self._search_pg(text, fields, start, end, ts_field, size)  # pragma: no cover
        needle = text.lower() if text else None
        out: list[dict[str, Any]] = []
        for doc in self._docs:
            if needle is not None and needle not in self._flatten(doc):
                continue
            if fields and not self._match_fields(doc, fields):
                continue
            if (start is not None or end is not None):
                ts = doc.get(ts_field)
                if ts is None:
                    continue
                if start is not None and ts < start:
                    continue
                if end is not None and ts > end:
                    continue
            out.append(doc)
            if len(out) >= size:
                break
        return out

    @staticmethod
    def _match_fields(doc: dict, fields: dict) -> bool:
        for k, v in fields.items():
            dv = doc.get(k)
            want = getattr(v, "value", v)  # accept enum members
            if dv != want:
                return False
        return True

    @classmethod
    def _flatten(cls, obj: Any) -> str:
        """Lower-cased flattened string of all scalar values (for substring search)."""
        parts: list[str] = []
        if isinstance(obj, dict):
            for v in obj.values():
                parts.append(cls._flatten(v))
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                parts.append(cls._flatten(v))
        else:
            parts.append(str(obj))
        return " ".join(parts).lower()

    def count(self) -> int:
        if self._es is not None:  # pragma: no cover
            try:
                return int(self._es.count(index=self.index).get("count", 0))
            except Exception:
                return 0
        return len(self._docs)

    # ------------------------------------------------- elasticsearch impls (lazy)
    def _index_pg(self, doc: dict) -> dict:  # pragma: no cover
        try:
            self._es.index(index=self.index, document=doc)
        except Exception:
            log.exception("elasticsearch index failed; buffering in memory")
            self._docs.append(doc)
        return doc

    @staticmethod
    def _elastic():  # pragma: no cover - imported only on the live backend path
        from ..integrations import elastic as _elastic

        return _elastic

    def _search_pg(self, text, fields, start, end, ts_field, size):  # pragma: no cover
        # Query-DSL construction lives in the single canonical builder module
        # :mod:`disastermind.integrations.elastic` (imported lazily — no import-time
        # or network dependency) so the audit search body is defined in one place.
        body = self._elastic().audit_search_body(
            text,
            fields=fields,
            start=start,
            end=end,
            ts_field=ts_field,
            size=size,
        )
        try:
            res = self._es.search(index=self.index, body=body)
            return [h["_source"] for h in res["hits"]["hits"]]
        except Exception:
            log.exception("elasticsearch search failed")
            return []
