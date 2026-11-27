from __future__ import annotations

import json

import pytest

from core.contracts import TaskStatus
from core.runtime import AgentRuntime


def test_runtime_goal_creates_and_verifies_exact_workspace_file(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    monkeypatch.setattr(runtime.tool_router, "_confirmation", lambda _tool, _args: True)
    expected = "Fariz Agent Test\nTahap: 1\nStatus: berhasil"
    plan_payload = {
        "goal": "Create exact file",
        "tasks": [{
            "task_id": "create_file",
            "title": "Create uji_agent_01.txt with exact content",
            "dependencies": [],
            "execution_contract": {
                "objective": "Create, write, read, and verify exact content",
                "allowed_tools": ["buat_file_terverifikasi"],
                "allowed_capabilities": ["workspace.write", "workspace.read"],
                "max_tool_calls": 1,
                "retry_limit": 0,
                "evidence_required": True,
                "completion_conditions": [{"type": "evidence_success", "task_id": "create_file"}],
            },
        }],
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    }
    proposal = {
        "tool": "buat_file_terverifikasi",
        "action": "execute",
        "arguments": {"nama": "uji_agent_01.txt", "isi": expected},
    }
    monkeypatch.setattr(runtime.model_gateway, "generate_text", lambda *_a, **_k: json.dumps(plan_payload))

    def execute_from_task_contract(prompt, **_kwargs):
        assert '"allowed_tools": ["buat_file_terverifikasi"]' in prompt
        assert '"cari_teks"' not in prompt
        return json.dumps(proposal)

    monkeypatch.setattr(runtime.model_gateway, "text", execute_from_task_contract)

    plan, graph = runtime.run_goal(
        "Buat file uji_agent_01.txt dengan isi persis: " + expected,
        plan_id="plan-verified-workspace-file",
    )

    assert plan.status.value == "COMPLETED"
    assert graph.tasks["create_file"].status == TaskStatus.COMPLETED
    assert (workspace / "uji_agent_01.txt").read_text(encoding="utf-8") == expected
    execution = runtime.last_goal_execution["create_file"]
    assert execution["success"] is True
    assert execution["data"]["verified"] is True
    assert execution["data"]["isi_aktual"] == expected
    evidence = runtime.state_store.load_evidence_for_task("create_file")
    assert evidence and all(item["success"] for item in evidence)


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        ("", "STRING_TOO_SHORT: isi"),
        ("   ", "WHITESPACE_ONLY: isi"),
        ("\t\n", "WHITESPACE_ONLY: isi"),
    ],
)
def test_runtime_rejects_invalid_verified_content_before_approval(
    tmp_path, monkeypatch, content, expected_error
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    approvals = []

    def confirm(tool, arguments):
        approvals.append((tool, arguments))
        return True

    monkeypatch.setattr(runtime.tool_router, "_confirmation", confirm)
    plan_payload = {
        "goal": "Create exact file",
        "tasks": [{
            "task_id": "create_file",
            "title": "Create uji_agent_invalid.txt with supplied content",
            "dependencies": [],
            "execution_contract": {
                "objective": "Create a new file with meaningful supplied content",
                "allowed_tools": ["buat_file_terverifikasi"],
                "allowed_capabilities": ["workspace.write", "workspace.read"],
                "max_tool_calls": 1,
                "retry_limit": 0,
                "evidence_required": True,
                "completion_conditions": [{"type": "evidence_success", "task_id": "create_file"}],
            },
        }],
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    }
    proposal = {
        "tool": "buat_file_terverifikasi",
        "action": "execute",
        "arguments": {"nama": "uji_agent_invalid.txt", "isi": content},
    }
    monkeypatch.setattr(
        runtime.model_gateway,
        "generate_text",
        lambda *_a, **_k: json.dumps(plan_payload),
    )
    monkeypatch.setattr(
        runtime.model_gateway,
        "text",
        lambda *_a, **_k: json.dumps(proposal),
    )

    plan, graph = runtime.run_goal(
        "Buat file uji_agent_invalid.txt; isi tidak boleh kosong atau hanya whitespace",
        plan_id="plan-reject-invalid-verified-content",
    )

    assert plan.status.value == "FAILED"
    assert graph.tasks["create_file"].status == TaskStatus.FAILED
    assert approvals == []
    assert not (workspace / "uji_agent_invalid.txt").exists()
    execution = runtime.last_goal_execution["create_file"]
    assert execution["success"] is False
    assert execution["status"] == "schema_invalid"
    assert expected_error in execution["error"]
