from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from core.state_store import StateStore
from memory import MEMORY_KINDS, remember


class MemoryCandidateStatus(str, Enum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    task_id: str | None
    project_id: str | None
    kind: str
    key: str
    value: Any
    source: str | Mapping[str, Any]
    context: tuple[str, ...]
    reflection: str
    evidence_ids: tuple[str, ...]
    status: MemoryCandidateStatus = MemoryCandidateStatus.PENDING
    created_at: str = ""
    updated_at: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_context(context: Sequence[str] | str | None) -> tuple[str, ...]:
    if context is None:
        return ()
    values = (context,) if isinstance(context, str) else tuple(context)
    normalized = []
    for item in values:
        item = str(item).strip()
        if item and item not in normalized:
            normalized.append(item)
    return tuple(normalized[:32])


def _normalize_source(source: str | Mapping[str, Any]) -> str | dict[str, Any]:
    if isinstance(source, str):
        if not source.strip():
            raise ValueError("Candidate source cannot be empty")
        return source.strip()
    if isinstance(source, Mapping):
        result = dict(source)
        if not str(result.get("type", "")).strip():
            raise ValueError("Candidate source.type cannot be empty")
        return result
    raise TypeError("Candidate source must be a string or mapping")


def _candidate_from_row(row: dict[str, Any]) -> MemoryCandidate:
    payload = row["payload"]
    return MemoryCandidate(
        candidate_id=row["candidate_id"],
        task_id=row.get("task_id"),
        project_id=row.get("project_id"),
        kind=payload["kind"],
        key=payload["key"],
        value=payload["value"],
        source=payload["source"],
        context=tuple(payload.get("context", ())),
        reflection=payload["reflection"],
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        status=MemoryCandidateStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def propose_memory_candidate(
    store: StateStore,
    *,
    key: str,
    value: Any,
    kind: str = "experience",
    reflection: str,
    source: str | Mapping[str, Any] = "agent",
    project_id: str | None = None,
    task_id: str | None = None,
    context: Sequence[str] | str | None = None,
    evidence_ids: Sequence[str] = (),
) -> MemoryCandidate:
    key = str(key).strip()
    reflection = str(reflection).strip()
    kind = str(kind).strip().lower()
    project_id = str(project_id).strip() if project_id is not None else None
    task_id = str(task_id).strip() if task_id is not None else None

    if not key:
        raise ValueError("Candidate key cannot be empty")
    if kind not in MEMORY_KINDS:
        raise ValueError(f"Unsupported memory candidate kind: {kind}")
    if not reflection:
        raise ValueError("Candidate reflection cannot be empty")

    evidence_ids = tuple(
        dict.fromkeys(
            str(evidence_id).strip()
            for evidence_id in evidence_ids
            if str(evidence_id).strip()
        )
    )[:32]
    candidate = MemoryCandidate(
        candidate_id=f"cand-{uuid.uuid4().hex}",
        task_id=task_id,
        project_id=project_id,
        kind=kind,
        key=key,
        value=value,
        source=_normalize_source(source),
        context=_normalize_context(context),
        reflection=reflection,
        evidence_ids=evidence_ids,
        status=MemoryCandidateStatus.PENDING,
        created_at=_now(),
        updated_at=_now(),
    )
    payload = {
        "key": candidate.key,
        "value": candidate.value,
        "kind": candidate.kind,
        "source": candidate.source,
        "context": list(candidate.context),
        "reflection": candidate.reflection,
        "evidence_ids": list(candidate.evidence_ids),
    }
    store.save_memory_candidate(
        candidate_id=candidate.candidate_id,
        task_id=candidate.task_id,
        project_id=candidate.project_id,
        status=candidate.status.value,
        payload=payload,
    )
    return candidate


def list_memory_candidates(
    store: StateStore,
    *,
    status: MemoryCandidateStatus | str | None = MemoryCandidateStatus.PENDING,
    task_id: str | None = None,
    project_id: str | None = None,
) -> list[MemoryCandidate]:
    normalized_status = None
    if status is not None:
        normalized_status = (
            status.value if isinstance(status, MemoryCandidateStatus) else str(status)
        )
        normalized_status = MemoryCandidateStatus(normalized_status).value
    return [
        _candidate_from_row(row)
        for row in store.load_memory_candidates(
            status=normalized_status,
            task_id=task_id,
            project_id=project_id,
        )
    ]


def commit_memory_candidate(
    store: StateStore,
    candidate_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    reason = str(reason).strip()
    if not reason:
        raise ValueError("Candidate commit requires a reason")

    row = store.load_memory_candidate(candidate_id)
    if row is None:
        raise KeyError(f"Unknown memory candidate: {candidate_id}")

    current = MemoryCandidateStatus(row["status"])
    if current == MemoryCandidateStatus.COMMITTED:
        return {
            "status": "already_committed",
            "success": True,
            "candidate_id": candidate_id,
            "memory_id": row["payload"].get("memory_id"),
        }
    if current != MemoryCandidateStatus.PENDING:
        raise ValueError(f"Candidate is not pending: {current.value}")

    evidence_ids = tuple(row["payload"].get("evidence_ids", ()))
    for evidence_id in evidence_ids:
        evidence = store.load_evidence(evidence_id)
        if evidence is None:
            raise ValueError(f"Candidate evidence not found: {evidence_id}")
        if not evidence["success"]:
            raise ValueError(f"Candidate evidence is unsuccessful: {evidence_id}")
        candidate_task_id = row.get("task_id")
        if candidate_task_id is not None and evidence["task_id"] != candidate_task_id:
            raise ValueError(f"Candidate evidence belongs to another task: {evidence_id}")

    payload = row["payload"]
    source = payload["source"]
    if isinstance(source, Mapping):
        source = dict(source)
        source["candidate_id"] = candidate_id
        source["commit_reason"] = reason
    else:
        source = {
            "type": source,
            "candidate_id": candidate_id,
            "commit_reason": reason,
        }

    result = remember(
        payload["key"],
        payload["value"],
        kind=payload["kind"],
        source=source,
        project_id=row.get("project_id"),
        task_id=row.get("task_id"),
        context=payload.get("context", ()),
    )
    payload = dict(payload)
    payload["memory_id"] = result["memory_id"]
    payload["commit_reason"] = reason
    payload["committed_at"] = _now()
    store.save_memory_candidate(
        candidate_id=candidate_id,
        task_id=row.get("task_id"),
        project_id=row.get("project_id"),
        status=MemoryCandidateStatus.COMMITTED.value,
        payload=payload,
    )
    return {
        "status": "success",
        "success": True,
        "candidate_id": candidate_id,
        "memory_id": result["memory_id"],
    }


def reject_memory_candidate(
    store: StateStore,
    candidate_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    reason = str(reason).strip()
    if not reason:
        raise ValueError("Candidate rejection requires a reason")

    row = store.load_memory_candidate(candidate_id)
    if row is None:
        raise KeyError(f"Unknown memory candidate: {candidate_id}")
    current = MemoryCandidateStatus(row["status"])
    if current == MemoryCandidateStatus.REJECTED:
        return {
            "status": "already_rejected",
            "success": True,
            "candidate_id": candidate_id,
        }
    if current == MemoryCandidateStatus.COMMITTED:
        raise ValueError("Committed candidate cannot be rejected")

    payload = dict(row["payload"])
    payload["rejection_reason"] = reason
    payload["rejected_at"] = _now()
    store.save_memory_candidate(
        candidate_id=candidate_id,
        task_id=row.get("task_id"),
        project_id=row.get("project_id"),
        status=MemoryCandidateStatus.REJECTED.value,
        payload=payload,
    )
    return {
        "status": "success",
        "success": True,
        "candidate_id": candidate_id,
    }


def build_task_experience_candidate(
    store: StateStore,
    *,
    task_id: str,
    title: str,
    project_id: str | None,
    result: Any,
    attempts: int,
    tool_calls: int,
    evidence_ids: Sequence[str] = (),
) -> MemoryCandidate:
    return propose_memory_candidate(
        store,
        key=f"task:{task_id}:experience",
        value={
            "task_id": task_id,
            "title": title,
            "result": result,
            "attempts": attempts,
            "tool_calls": tool_calls,
            "evidence_ids": list(evidence_ids),
        },
        kind="experience",
        reflection=(
            f"Task '{title}' completed after {attempts} attempt(s) "
            f"and {tool_calls} tool call(s)."
        ),
        source={"type": "task_completion", "task_id": task_id},
        project_id=project_id,
        task_id=task_id,
        context=(title,),
        evidence_ids=evidence_ids,
    )
