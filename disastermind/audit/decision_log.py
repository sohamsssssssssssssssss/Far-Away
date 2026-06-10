"""Tamper-evident decision logging (PRD Step 9).

Every decision is logged with full reasoning chain, ISO-8601 timestamp,
sender/recipient, priority, message type and TTL. We also expose a hook for
per-prediction SHAP values (explainability requirement).

Backends:
  * Always: append-only JSONL on disk (the durable local trail).
  * Optional: Elasticsearch (full audit trail) when ``elasticsearch_url`` set.

Tamper-evidence: each record carries the SHA-256 of the previous record,
forming a hash chain. Any retroactive edit breaks the chain — detectable by
:meth:`verify_chain`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from ..core.contracts import Message

log = logging.getLogger("disastermind.audit")

GENESIS = "0" * 64


class DecisionLogger:
    def __init__(self, path: str = "./audit.jsonl", elasticsearch_url: str = "") -> None:
        self.path = path
        self.elasticsearch_url = elasticsearch_url
        self._prev_hash = self._load_tip()
        self._es = self._connect_es() if elasticsearch_url else None

    # ---------------------------------------------------------------- factory
    @classmethod
    def null(cls) -> DecisionLogger:
        """A logger that records to an in-memory list only (tests/degraded)."""
        inst = cls.__new__(cls)
        inst.path = ""
        inst.elasticsearch_url = ""
        inst._prev_hash = GENESIS
        inst._es = None
        inst.memory: list[dict] = []
        return inst

    # ---------------------------------------------------------------- helpers
    def _load_tip(self) -> str:
        if not os.path.exists(self.path):
            return GENESIS
        tip = GENESIS
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        tip = json.loads(line).get("_hash", tip)
        except Exception:
            log.exception("could not read audit tip; starting fresh chain")
        return tip

    def _connect_es(self):  # pragma: no cover - optional dependency
        try:
            from elasticsearch import Elasticsearch  # type: ignore

            return Elasticsearch(self.elasticsearch_url)
        except Exception:
            log.warning("elasticsearch unavailable; JSONL-only audit trail")
            return None

    @staticmethod
    def _hash(prev: str, body: str) -> str:
        return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ write
    def record(self, message: Message) -> dict[str, Any]:
        rec = message.to_dict()
        body = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        rec["_prev"] = self._prev_hash
        rec["_hash"] = self._hash(self._prev_hash, body)
        self._prev_hash = rec["_hash"]
        self._persist(rec)
        return rec

    def log_prediction(
        self, model: str, inputs: dict, prediction: Any, shap: dict, incident_id: str | None = None
    ) -> dict[str, Any]:
        """SHAP-annotated model prediction log (PRD Step 9 explainability)."""
        rec = {
            "kind": "prediction",
            "model": model,
            "inputs": inputs,
            "prediction": prediction,
            "shap": shap,
            "incident_id": incident_id,
        }
        body = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        rec["_prev"] = self._prev_hash
        rec["_hash"] = self._hash(self._prev_hash, body)
        self._prev_hash = rec["_hash"]
        self._persist(rec)
        return rec

    def _persist(self, rec: dict) -> None:
        if getattr(self, "memory", None) is not None and not self.path:
            self.memory.append(rec)
            return
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except Exception:
            log.exception("failed to persist audit record")
        if self._es is not None:  # pragma: no cover
            try:
                self._es.index(index="disastermind-audit", document=rec)
            except Exception:
                log.exception("elasticsearch index failed")

    # ----------------------------------------------------------------- verify
    def verify_chain(self) -> bool:
        """Re-walk the JSONL chain; return True iff untampered."""
        if not self.path or not os.path.exists(self.path):
            return True
        prev = GENESIS
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                stored = rec.pop("_hash")
                rec_prev = rec.pop("_prev")
                body = json.dumps(rec, sort_keys=True, separators=(",", ":"))
                if rec_prev != prev or self._hash(prev, body) != stored:
                    return False
                prev = stored
        return True
