from __future__ import annotations

import json

import pytest

from core.memory_service import MemoryService
from core.contracts import ToolResult
from verification.evidence import EvidenceStore
from core.reflection import (
    MAX_REFLECTION_OUTPUT_CHARS,
    ReflectionError,
    ReflectionService,
)
from memory import recall, remember, search_memory


def _remember(key, value, *, project_id=None, task_id=None):
    return remember(
        key,
        value,
        memory_type="fact",
        project_id=project_id,
        task_id=task_id,
        context={"suite": "memory-audit"},
        source={"kind": "execution", "ref": f"evidence:{key}:{task_id or project_id or 'global'}"},
        provenance={"reason": "verified test fixture"},
        importance=0.8,
        confidence=0.9,
    )


def test_memory_retrieval_is_hierarchical_and_never_crosses_project_or_task(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    _remember("shared", "global")
    _remember("shared", "project A", project_id="project-a")
    _remember("shared", "project B", project_id="project-b")
    _remember("shared", "task A1", project_id="project-a", task_id="task-a1")
    _remember("shared", "task A2", project_id="project-a", task_id="task-a2")
    _remember("shared", "task B1", project_id="project-b", task_id="task-b1")

    global_rows = search_memory("shared", limit=20)["results"]
    assert {row["record"]["value"] for row in global_rows} == {"global"}

    project_rows = search_memory(
        "shared", project_id="project-a", limit=20
    )["results"]
    assert {row["record"]["value"] for row in project_rows} == {
        "global", "project A"
    }

    task_rows = search_memory(
        "shared", project_id="project-a", task_id="task-a1", limit=20
    )["results"]
    assert {row["record"]["value"] for row in task_rows} == {
        "global", "project A", "task A1"
    }
    assert "task A2" not in {row["record"]["value"] for row in task_rows}
    assert "project B" not in {row["record"]["value"] for row in task_rows}
    assert "task B1" not in {row["record"]["value"] for row in task_rows}


def test_recall_without_scope_returns_only_global_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    _remember("same-key", "global")
    _remember("same-key", "private project", project_id="project-a")
    _remember("same-key", "private task", project_id="project-a", task_id="task-a")

    assert recall("same-key")["value"] == "global"
    assert recall("same-key", project_id="project-a")["value"] == "private project"
    assert recall(
        "same-key", project_id="project-a", task_id="task-a"
    )["value"] == "private task"


def test_reflection_rejects_oversized_or_unbounded_candidates():
    payload = {
        "candidates": [
            {
                "key": "oversized-value",
                "value": "x" * 6001,
                "memory_type": "fact",
                "evidence_refs": ["ev-1"],
            },
            {
                "key": "too-many-tags",
                "value": "bounded",
                "memory_type": "fact",
                "evidence_refs": ["ev-1"],
                "tags": [f"tag-{index}" for index in range(21)],
            },
            {
                "key": "oversized-key-" + ("k" * 200),
                "value": "bounded",
                "memory_type": "fact",
                "evidence_refs": ["ev-1"],
            },
        ]
    }
    assert ReflectionService is not None
    from core.reflection import validate_candidates

    assert validate_candidates(payload, allowed_evidence_refs={"ev-1"}) == []


def test_reflection_rejects_model_response_over_output_limit():
    service = ReflectionService(lambda **_: "x" * (MAX_REFLECTION_OUTPUT_CHARS + 1))
    with pytest.raises(ReflectionError, match="REFLECTION_OUTPUT_TOO_LARGE"):
        service.reflect(experience={"success": True}, evidence_refs=["ev-1"])


def _evidence_store(evidence_id, task_id):
    store = EvidenceStore()
    store.record(
        evidence_id=evidence_id,
        task_id=task_id,
        tool="test_tool",
        action="execute",
        result=ToolResult(True, "SUCCESS", "test_tool"),
    )
    return store


def test_reflection_is_queued_until_human_approval_and_survives_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    candidate = {
        "key": "project.build.preflight",
        "value": "Check the SDK before building",
        "memory_type": "fact",
        "summary": "Build preflight check",
        "confidence": 0.9,
        "importance": 0.8,
        "retention": "durable",
        "evidence_refs": ["ev-verified-1"],
        "tags": ["build"],
        "scope": "project",
        "risk": "low",
    }

    def model_call(**_):
        return json.dumps({"candidates": [candidate]})

    evidence_store = _evidence_store("ev-verified-1", "task-a1")
    service = MemoryService(project_id="project-a", evidence_store=evidence_store)
    result = service.reflect_and_commit(
        model_call=model_call,
        experience={"tool": "run_command", "success": True},
        evidence_refs=["ev-verified-1"],
        project_id="project-a",
        task_id="task-a1",
        source={"kind": "execution", "ref": "ev-verified-1"},
    )

    assert result["success"] is True
    assert result["committed"] == []
    assert result["rejected"][0]["status"] == "pending_review"
    review_id = result["rejected"][0]["review_id"]
    assert search_memory(
        "Check the SDK", project_id="project-a", limit=10
    )["results"] == []

    restarted = MemoryService(project_id="project-a", evidence_store=evidence_store)
    assert any(item["id"] == review_id for item in restarted.pending_reflections())
    approved = restarted.review_reflection(review_id, approve=True)
    assert approved["status"] == "approved"
    assert approved["record"]["value"] == candidate["value"]
    assert not any(
        item["id"] == review_id for item in restarted.pending_reflections()
    )


def test_rejected_reflection_never_enters_active_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    candidate = {
        "key": "untrusted.claim",
        "value": "This claim should not be stored",
        "memory_type": "fact",
        "evidence_refs": ["ev-1"],
    }
    evidence_store = _evidence_store("ev-1", "task-a")
    service = MemoryService(project_id="project-a", evidence_store=evidence_store)
    result = service.reflect_and_commit(
        model_call=lambda **_: json.dumps({"candidates": [candidate]}),
        experience={"success": False},
        evidence_refs=["ev-1"],
        project_id="project-a",
        task_id="task-a",
        source={"kind": "execution", "ref": "ev-1"},
    )
    review_id = result["rejected"][0]["review_id"]

    rejected = service.review_reflection(review_id, approve=False)
    assert rejected["status"] == "rejected"
    assert search_memory(
        "This claim", project_id="project-a", task_id="task-a"
    )["results"] == []



def test_reflection_rejects_missing_or_cross_task_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    evidence_store = _evidence_store("real-evidence", "task-a")
    service = MemoryService(project_id="project-a", evidence_store=evidence_store)

    missing = service.reflect_and_commit(
        model_call=lambda **_: json.dumps({"candidates": []}),
        experience={"success": True},
        evidence_refs=["forged-evidence"],
        project_id="project-a",
        task_id="task-a",
    )
    mismatch = service.reflect_and_commit(
        model_call=lambda **_: json.dumps({"candidates": []}),
        experience={"success": True},
        evidence_refs=["real-evidence"],
        project_id="project-a",
        task_id="task-b",
    )

    assert missing["reason"] == "EVIDENCE_NOT_FOUND"
    assert mismatch["reason"] == "EVIDENCE_TASK_MISMATCH"
