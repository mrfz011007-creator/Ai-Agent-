from __future__ import annotations

import json

from core.conversation import ConversationService
from core.runtime import AgentRuntime
from core.state_store import StateStore


def test_failed_goal_is_available_to_followup_chat_after_restart(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_path = tmp_path / "state.sqlite3"
    conversation_id = "goal-continuity-regression"
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("AI_AGENT_CONVERSATION_ID", conversation_id)

    runtime = AgentRuntime.create(state_path=state_path)
    runtime.tool_router._confirmation = lambda _tool, _args: False

    original_goal = (
        "Buat file uji_agent_02.txt di workspace dengan isi persis berikut:\n"
        "Fariz Agent Test\nTahap: 2\nStatus: berhasil\n\n"
        "Baca kembali file tersebut dan verifikasi seluruh isinya sama persis."
    )
    plan_payload = {
        "goal": "Model rewrote the goal",
        "tasks": [{
            "task_id": "create_file",
            "title": "Create requested file",
            "dependencies": [],
            "execution_contract": {
                "objective": "Create and verify requested file",
                "allowed_tools": ["buat_file_terverifikasi"],
                "allowed_capabilities": ["workspace.write", "workspace.read"],
                "max_tool_calls": 1,
                "retry_limit": 0,
                "evidence_required": True,
                "completion_conditions": [
                    {"type": "evidence_success", "task_id": "create_file"}
                ],
            },
        }],
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    }
    proposal = {
        "tool": "buat_file_terverifikasi",
        "action": "execute",
        "arguments": {
            "nama": "uji_agent_02.txt",
            "isi": "Fariz Agent Test\nTahap: 2\nStatus: berhasil",
        },
    }
    monkeypatch.setattr(
        runtime.model_gateway,
        "generate_text",
        lambda *_args, **_kwargs: json.dumps(plan_payload),
    )
    monkeypatch.setattr(
        runtime.model_gateway,
        "text",
        lambda *_args, **_kwargs: json.dumps(proposal),
    )

    plan, graph = runtime.run_goal(
        original_goal,
        plan_id="plan-goal-chat-continuity",
    )

    assert plan.status.value == "FAILED"
    assert graph.tasks["create_file"].status.value == "FAILED"
    assert runtime.last_goal_execution["create_file"]["error"] == "User denied tool execution"
    assert not (workspace / "uji_agent_02.txt").exists()

    # Reopen the database to prove the context survives a process restart.
    restarted_store = StateStore(state_path)
    prompts = []

    class FakeGateway:
        def generate_text(self, *, prompt, system_instruction):
            prompts.append((prompt, system_instruction))
            return "Tindakan sebelumnya ditolak."

    response = ConversationService(
        FakeGateway(),
        restarted_store,
        conversation_id=conversation_id,
    ).answer("Kenapa tugas tadi gagal?")

    history = json.loads(prompts[0][0])["conversation_history"]
    assert response == "Tindakan sebelumnya ditolak."
    assert any(row["role"] == "user" and row["content"] == original_goal for row in history)
    recorded_outcome = "\\n".join(row["content"] for row in history if row["role"] == "assistant")
    assert "goal_execution_result" in recorded_outcome
    assert "FAILED" in recorded_outcome
    assert "User denied tool execution" in recorded_outcome
    assert "Do not ask the user to repeat" in prompts[0][1]



def test_resumed_goal_outcome_is_saved_to_conversation_history(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_path = tmp_path / "state.sqlite3"
    conversation_id = "resume-goal-continuity"
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("AI_AGENT_CONVERSATION_ID", conversation_id)
    runtime = AgentRuntime.create(state_path=state_path)
    runtime.tool_router._confirmation = lambda _tool, _args: False

    goal = (
        "Buat file resume.txt dengan isi persis berikut:\\n"
        "Catatan resume\\nTahap: 1"
    )
    plan_payload = {
        "goal": "Create exact file",
        "tasks": [{
            "task_id": "create_file",
            "title": "Create requested file",
            "dependencies": [],
            "execution_contract": {
                "objective": "Create and verify requested file",
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
        "arguments": {"nama": "resume.txt", "isi": "Catatan resume\\nTahap: 1"},
    }
    monkeypatch.setattr(runtime.model_gateway, "generate_text", lambda *_a, **_k: json.dumps(plan_payload))
    monkeypatch.setattr(runtime.model_gateway, "text", lambda *_a, **_k: json.dumps(proposal))

    waiting_plan, _ = runtime.run_goal(goal, plan_id="plan-resume-continuity", max_steps=0)
    assert waiting_plan.status.value == "WAITING"

    resumed_plan, graph = runtime.resume("plan-resume-continuity")
    assert resumed_plan.status.value == "FAILED"
    assert graph.tasks["create_file"].status.value == "FAILED"
    assert not (workspace / "resume.txt").exists()

    prompts = []
    class FakeGateway:
        def generate_text(self, *, prompt, system_instruction):
            prompts.append(prompt)
            return "Tugas sebelumnya ditolak."

    response = ConversationService(
        FakeGateway(),
        StateStore(state_path),
        conversation_id=conversation_id,
    ).answer("Apa hasil tugas yang dilanjutkan tadi?")
    history = json.loads(prompts[0])["conversation_history"]
    assert response == "Tugas sebelumnya ditolak."
    outcome = "\\n".join(row["content"] for row in history if row["role"] == "assistant")
    assert "goal_execution_result" in outcome
    assert "FAILED" in outcome
    assert "User denied tool execution" in outcome
