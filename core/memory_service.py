from __future__ import annotations

from typing import Any, Mapping

from memory import invalidate_memory, remember, search_memory, update_memory
from core.reflection import ReflectionService


class MemoryService:
    """Bounded adapter between agent lifecycle and persistent structured memory."""

    def __init__(self, *, project_id: str | None = None):
        self.project_id = project_id

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
                rejected.append({"key": candidate.key, "reason": "REFLECTION_HIGH_RISK_REQUIRES_REVIEW"})
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
    return [{"type": item["record"].get("type"), "key": item["record"].get("key"),
             "value": item["record"].get("value"), "summary": item["record"].get("summary"),
             "project_id": item["record"].get("project_id"), "task_id": item["record"].get("task_id"),
             "context": item["record"].get("context"), "source": item["record"].get("source"),
             "provenance": item["record"].get("provenance"), "importance": item["record"].get("importance"),
             "confidence": item["record"].get("confidence"), "retention": item["record"].get("retention"),
             "version": item["record"].get("version"), "updated_at": item["record"].get("updated_at"),
             "tags": item["record"].get("tags")} for item in result.get("results", [])]
