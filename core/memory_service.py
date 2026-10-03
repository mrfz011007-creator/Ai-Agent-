from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from memory import (
    DEFAULT_AUTO_COMPACT_BYTES,
    compact_memory_if_needed,
    invalidate_memory,
    remember,
    search_memory,
    update_memory,
)

MAX_EXPERIENCE_VALUE_CHARS = 6000
MAX_PROMPT_RECORD_VALUE_CHARS = 3000
from core.reflection import ReflectionService
from core import reflection_queue


class MemoryService:
    """Bounded adapter between agent lifecycle and persistent structured memory."""

    def __init__(self, *, project_id: str | None = None):
        self.project_id = project_id
        # Clean oversized legacy/runtime memory at startup without touching
        # the store when it is already below the configured threshold.
        compact_memory_if_needed()

    def retrieve(self, query: str, *, project_id: str | None = None,
                 task_id: str | None = None, context: Mapping[str, Any] | None = None,
                 limit: int = 8) -> dict[str, Any]:
        return search_memory(query, project_id=project_id if project_id is not None else self.project_id,
                             task_id=task_id, context=context, limit=limit)

    def propose(self, *, key: str, value: Any, memory_type: str = "fact",
                task_id: str | None = None, context: Mapping[str, Any] | None = None,
                source: Mapping[str, Any] | str | None = None,
                provenance: Mapping[str, Any] | None = None,
                importance: float | None = None, confidence: float | None = None,
                retention: str = "normal", summary: str | None = None,
                evidence_refs: list[str] | None = None) -> dict[str, Any]:
        return {"key": key, "value": value, "memory_type": memory_type,
                "project_id": self.project_id, "task_id": task_id,
                "context": context or {}, "source": source,
                "provenance": provenance or {"reason": "memory_proposal"},
                "importance": importance, "confidence": confidence,
                "retention": retention, "summary": summary,
                "evidence_refs": evidence_refs or []}

    def commit_proposal(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        return remember(proposal["key"], proposal["value"],
                        memory_type=proposal.get("memory_type", "fact"),
                        project_id=proposal.get("project_id", self.project_id),
                        task_id=proposal.get("task_id"), context=proposal.get("context"),
                        source=proposal.get("source"), provenance=proposal.get("provenance"),
                        importance=proposal.get("importance"), confidence=proposal.get("confidence"),
                        retention=proposal.get("retention", "normal"), summary=proposal.get("summary"),
                        evidence_refs=proposal.get("evidence_refs"))

    def record_experience(self, *, key: str, value: Any, task_id: str | None = None,
                          context: Mapping[str, Any] | None = None,
                          source: Mapping[str, Any] | str | None = None,
                          provenance: Mapping[str, Any] | None = None,
                          tags: list[str] | None = None,
                          project_id: str | None = None,
                          importance: float | None = None, confidence: float | None = None,
                          retention: str = "normal", summary: str | None = None,
                          evidence_refs: list[str] | None = None) -> dict[str, Any]:
        serialized = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
        if len(serialized) > MAX_EXPERIENCE_VALUE_CHARS:
            value = {
                "_truncated": True,
                "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "original_chars": len(serialized),
                "preview": serialized[:MAX_EXPERIENCE_VALUE_CHARS] + "...[TRUNCATED]",
            }
        return remember(key, value, memory_type="experience",
                        project_id=project_id if project_id is not None else self.project_id,
                        task_id=task_id, context=context, source=source, provenance=provenance,
                        tags=tags, importance=importance, confidence=confidence,
                        retention=retention, summary=summary, evidence_refs=evidence_refs)

    def reflect_and_commit(self, *, model_call, experience: Mapping[str, Any], evidence_refs: list[str] | tuple[str, ...],
                           related_memory: list[Mapping[str, Any]] | None = None, project_id: str | None = None,
                           task_id: str | None = None, context: Mapping[str, Any] | None = None,
                           source: Mapping[str, Any] | str | None = None) -> dict[str, Any]:
        """Reflect on one verified experience and commit only bounded candidates."""
        service = ReflectionService(model_call)
        try:
            candidates = service.reflect(
                experience=experience,
                evidence_refs=evidence_refs,
                related_memory=related_memory,
            )
        except Exception as error:
            # Reflection is advisory. A model/reflection failure must never fail the task.
            return {"status": "reflection_failed", "success": False, "reason": str(error), "committed": []}

        committed = []
        rejected = []
        for candidate in candidates:
            # High-risk proposals never receive autonomous persistence authority.
            if candidate.risk == "high":
                item = reflection_queue.enqueue({"key": candidate.key, "value": candidate.value,
                    "memory_type": candidate.memory_type, "summary": candidate.summary,
                    "confidence": candidate.confidence, "importance": candidate.importance,
                    "retention": candidate.retention, "evidence_refs": list(candidate.evidence_refs),
                    "tags": list(candidate.tags), "scope": candidate.scope, "risk": candidate.risk,
                    "project_id": project_id if project_id is not None else self.project_id,
                    "task_id": task_id, "context": context or {}, "source": source},
                    reason="REFLECTION_HIGH_RISK_REQUIRES_REVIEW")
                rejected.append({"key": candidate.key, "status": "pending_review", "review_id": item["id"]})
                continue
            existing = search_memory(candidate.key, memory_type=candidate.memory_type,
                project_id=project_id if project_id is not None else self.project_id, task_id=task_id, limit=5)
            if any(item["record"].get("value") == candidate.value for item in existing.get("results", [])):
                rejected.append({"key": candidate.key, "status": "duplicate", "reason": "REFLECTION_DUPLICATE"})
                continue
            result = self.commit_proposal({
                "key": candidate.key,
                "value": candidate.value,
                "memory_type": candidate.memory_type,
                "project_id": project_id if project_id is not None else self.project_id,
                "task_id": task_id,
                "context": context or {},
                "source": source,
                "provenance": {
                    "reason": "bounded_reflection",
                    "scope": candidate.scope,
                    "risk": candidate.risk,
                    "reflection_evidence_refs": list(candidate.evidence_refs),
                },
                "importance": candidate.importance,
                "confidence": candidate.confidence,
                "retention": candidate.retention,
                "summary": candidate.summary,
                "evidence_refs": list(candidate.evidence_refs),
            })
            (committed if result.get("success") else rejected).append(result)
        return {"status": "success", "success": True, "committed": committed, "rejected": rejected}

    def review_reflection(self, review_id: str, *, approve: bool, reviewer: str = "human") -> dict[str, Any]:
        pending = {item["id"]: item for item in reflection_queue.list_pending()}
        item = pending.get(review_id)
        if item is None:
            return {"status": "not_found", "success": False, "review_id": review_id}
        candidate = item["candidate"]
        if not approve:
            resolved = reflection_queue.resolve(review_id, status="rejected", resolution=reviewer)
            return {"status": "rejected", "success": True, "review": resolved}
        result = self.commit_proposal(candidate)
        if not result.get("success"):
            return {"status": "commit_rejected", "success": False, "result": result}
        resolved = reflection_queue.resolve(review_id, status="approved", resolution=reviewer)
        return {"status": "approved", "success": True, "record": result.get("record"), "review": resolved}

    def pending_reflections(self) -> list[dict[str, Any]]:
        return reflection_queue.list_pending()

    def update(self, record_id: str, value: Any,
               *, provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return update_memory(record_id, value, provenance=provenance)

    def invalidate(self, record_id: str, *, reason: str = "invalidated") -> dict[str, Any]:
        return invalidate_memory(record_id, reason=reason)

    def summarize_candidate(self, *, key: str, value: Any, memory_type: str = "experience",
                            source: Mapping[str, Any] | str | None = None, task_id: str | None = None,
                            evidence_refs: list[str] | None = None) -> dict[str, Any]:
        text = str(value).strip()
        summary = text if len(text) <= 500 else text[:497].rstrip() + "..."
        return self.propose(key=key, value=value, memory_type=memory_type, task_id=task_id,
                            source=source, provenance={"reason": "summarized_candidate"},
                            summary=summary, evidence_refs=evidence_refs or [])


def memory_prompt_context(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return bounded memory rows suitable for model context."""
    rows = []
    for item in result.get("results", []):
        record = item["record"]
        value = record.get("value")
        serialized = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
        if len(serialized) > MAX_PROMPT_RECORD_VALUE_CHARS:
            value = {
                "_truncated": True,
                "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "original_chars": len(serialized),
                "preview": serialized[:MAX_PROMPT_RECORD_VALUE_CHARS] + "...[TRUNCATED]",
            }
        rows.append({
            "type": record.get("type"),
            "key": record.get("key"),
            "value": value,
            "summary": record.get("summary"),
            "project_id": record.get("project_id"),
            "task_id": record.get("task_id"),
            "context": record.get("context"),
            "source": record.get("source"),
            "provenance": record.get("provenance"),
            "importance": record.get("importance"),
            "confidence": record.get("confidence"),
            "retention": record.get("retention"),
            "version": record.get("version"),
            "updated_at": record.get("updated_at"),
            "tags": record.get("tags"),
        })
    return rows
