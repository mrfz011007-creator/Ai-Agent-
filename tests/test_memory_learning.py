from pathlib import Path

import pytest

from core.memory_learning import (
    MemoryCandidateStatus,
    build_task_experience_candidate,
    commit_memory_candidate,
    list_memory_candidates,
    propose_memory_candidate,
    reject_memory_candidate,
)
from core.runtime import AgentRuntime
from memory import recall


def test_memory_candidate_persists_across_restart(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    state_path = tmp_path / "state.sqlite3"

    runtime1 = AgentRuntime.create(state_path=state_path)
    candidate = runtime1.propose_memory_candidate(
        key="lesson",
        value={"rule": "verify artifacts"},
        kind="experience",
        reflection="Artifact verification was required before completion.",
        source={"type": "model", "ref": "task-1"},
        project_id="launcher",
        task_id="task-1",
        context=["build"],
    )

    runtime2 = AgentRuntime.create(state_path=state_path)
    restored = runtime2.list_memory_candidates(
        project_id="launcher",
        task_id="task-1",
    )

    assert len(restored) == 1
    assert restored[0].candidate_id == candidate.candidate_id
    assert restored[0].status == MemoryCandidateStatus.PENDING
    assert restored[0].reflection == candidate.reflection


def test_memory_candidate_does_not_enter_long_term_memory_before_commit(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    candidate = runtime.propose_memory_candidate(
        key="pending_lesson",
        value="not yet committed",
        kind="experience",
        reflection="Pending learning candidate.",
        project_id="launcher",
        task_id="task-2",
    )

    assert recall(
        "pending_lesson",
        project_id="launcher",
        task_id="task-2",
    )["status"] == "not_found"

    result = runtime.commit_memory_candidate(
        candidate.candidate_id,
        reason="Verified as reusable learning.",
    )

    assert result["success"] is True
    assert recall(
        "pending_lesson",
        project_id="launcher",
        task_id="task-2",
    )["status"] == "success"


def test_candidate_commit_requires_successful_evidence_when_evidence_is_attached(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    candidate = runtime.propose_memory_candidate(
        key="evidence_lesson",
        value="verified",
        kind="experience",
        reflection="An evidence-backed learning.",
        project_id="launcher",
        task_id="task-3",
        evidence_ids=("missing-evidence",),
    )

    with pytest.raises(ValueError, match="evidence"):
        runtime.commit_memory_candidate(
            candidate.candidate_id,
            reason="Require evidence",
        )

    assert (
        runtime.list_memory_candidates(
            project_id="launcher",
            task_id="task-3",
        )[0].status
        == MemoryCandidateStatus.PENDING
    )


def test_rejected_candidate_never_reaches_long_term_memory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    candidate = runtime.propose_memory_candidate(
        key="rejected_lesson",
        value="discard",
        kind="experience",
        reflection="This should not become memory.",
        project_id="launcher",
        task_id="task-4",
    )

    result = runtime.reject_memory_candidate(
        candidate.candidate_id,
        reason="Not reusable.",
    )

    assert result["success"] is True
    assert recall(
        "rejected_lesson",
        project_id="launcher",
        task_id="task-4",
    )["status"] == "not_found"
    assert (
        runtime.list_memory_candidates(
            project_id="launcher",
            task_id="task-4",
            status=MemoryCandidateStatus.REJECTED,
        )[0].status
        == MemoryCandidateStatus.REJECTED
    )


def test_completed_task_can_produce_structured_experience_candidate(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    candidate = build_task_experience_candidate(
        runtime.state_store,
        task_id="task-5",
        title="Build APK",
        project_id="launcher",
        result={"status": "success"},
        attempts=2,
        tool_calls=4,
        evidence_ids=(),
    )

    assert candidate.kind == "experience"
    assert candidate.task_id == "task-5"
    assert candidate.project_id == "launcher"
    assert candidate.value["attempts"] == 2
    assert "completed" in candidate.reflection.lower()


def test_candidate_commit_rejects_successful_evidence_from_another_task(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    from core.contracts import ToolResult
    runtime.evidence_store.record(
        evidence_id="E-CAND-OTHER",
        task_id="other-task",
        tool="inspect",
        action="execute",
        result=ToolResult(True, "SUCCESS", "inspect"),
    )
    candidate = runtime.propose_memory_candidate(
        key="cross_task_lesson",
        value="must reject unrelated evidence",
        kind="experience",
        reflection="Evidence must belong to the candidate task.",
        task_id="candidate-task",
        evidence_ids=("E-CAND-OTHER",),
    )

    with pytest.raises(ValueError, match="another task"):
        runtime.commit_memory_candidate(
            candidate.candidate_id,
            reason="Reject cross-task evidence.",
        )

    assert runtime.list_memory_candidates(
        task_id="candidate-task",
        status=MemoryCandidateStatus.PENDING,
    )[0].status == MemoryCandidateStatus.PENDING
