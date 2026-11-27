"""Stage 5: exercise the complete goal lifecycle without bypassing execution gates.

The model replies are deterministic fixtures; planning validation, task scheduling,
execution contracts, confirmation, ToolRouter, GuardEngine, process sandbox,
evidence, verification, acceptance, checkpoints, and SQLite persistence are real.
"""

import json

from core.contracts import TaskStatus
from core.plan import PlanStatus
from core.runtime import AgentRuntime


def test_end_to_end_goal_uses_real_guarded_execution_and_survives_restart(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_path = workspace / "agent-state.sqlite3"
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("AI_AGENT_REFLECT_ON_SUCCESS", "0")

    runtime = AgentRuntime.create(state_path=state_path)

    plan_payload = {
        "goal": "validate full end-to-end agent path",
        "tasks": [
            {
                "task_id": "inspect",
                "title": "Inspect configured workspace",
                "dependencies": [],
                "execution_contract": {
                    "objective": "Read the current workspace path",
                    "allowed_tools": ["run_command"],
                    "allowed_capabilities": ["process.execute"],
                    "max_tool_calls": 1,
                    "retry_limit": 0,
                    "evidence_required": True,
                    "completion_conditions": [
                        {"type": "evidence_success", "task_id": "inspect"}
                    ],
                },
            },
            {
                "task_id": "verify",
                "title": "Identify the current sandbox user",
                "dependencies": ["inspect"],
                "execution_contract": {
                    "objective": "Read the current execution identity",
                    "allowed_tools": ["run_command"],
                    "allowed_capabilities": ["process.execute"],
                    "max_tool_calls": 1,
                    "retry_limit": 0,
                    "evidence_required": True,
                    "completion_conditions": [
                        {"type": "evidence_success", "task_id": "verify"}
                    ],
                },
            },
        ],
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    }

    planner_prompts = []

    def fake_plan_model(prompt, **kwargs):
        planner_prompts.append(prompt)
        return json.dumps(plan_payload)

    execution_prompts = []

    def fake_execution_model(prompt, **kwargs):
        execution_prompts.append(prompt)
        command = "pwd" if len(execution_prompts) == 1 else "whoami"
        return json.dumps(
            {
                "tool": "run_command",
                "action": "execute",
                "arguments": {
                    "command": command,
                    "cwd": str(workspace),
                    "timeout": 5,
                },
            }
        )

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_plan_model)
    monkeypatch.setattr(runtime.model_gateway, "text", fake_execution_model)

    confirmations = []

    def approve(tool, arguments):
        confirmations.append((tool, dict(arguments)))
        return True

    runtime.tool_router._confirmation = approve

    # Wrap the real runtime method to observe results; do not replace the router path.
    real_execute = runtime.execute_model_proposal
    execution_results = []

    def observe_real_execution(proposal, *, task_id, attempt_id=None):
        result = real_execute(
            proposal, task_id=task_id, attempt_id=attempt_id
        )
        execution_results.append((task_id, result))
        return result

    monkeypatch.setattr(runtime, "execute_model_proposal", observe_real_execution)

    plan, graph = runtime.run_goal(
        "validate full end-to-end agent path",
        plan_id="stage5-e2e",
        project_id="stage5-project",
        context={"workspace": "stage5"},
    )

    assert len(planner_prompts) == 1
    assert len(execution_prompts) == 2
    assert plan.status == PlanStatus.COMPLETED
    assert graph.tasks["inspect"].status == TaskStatus.COMPLETED
    assert graph.tasks["verify"].status == TaskStatus.COMPLETED
    assert graph.tasks["verify"].dependencies == ["inspect"]

    assert [task_id for task_id, _ in execution_results] == ["inspect", "verify"]
    assert len(confirmations) == 2
    assert all(tool == "run_command" for tool, _ in confirmations)

    for task_id, result in execution_results:
        assert result.success is True
        assert result.evidence_id
        assert result.data["success"] is True
        assert result.data["sandbox_mode"] in {"namespace", "hardened"}
        assert result.data["stdout"].strip()

    assert execution_results[0][1].data["stdout"].strip() == str(workspace.resolve())

    for task_id, _ in execution_results:
        task = graph.tasks[task_id]
        assert task.attempts == 1
        assert task.tool_calls == 1
        rows = runtime.state_store.load_evidence_for_task(task_id)
        assert len(rows) == 1
        assert rows[0]["success"] is True
        assert rows[0]["attempt_id"] == f"{task_id}:attempt:1"
        assert rows[0]["tool"] == "run_command"

        checkpoint = runtime.checkpoint_manager.latest(task_id)
        assert checkpoint is not None
        assert checkpoint["payload"]["event"] == "completed"

    # Successful execution is journaled as bounded, ephemeral memory.
    memory = runtime.memory.retrieve("execution", task_id="inspect", limit=5)
    memory_rows = memory.get("results", [])
    assert any(
        item.get("record", {}).get("source", {}).get("ref")
        == execution_results[0][1].evidence_id
        for item in memory_rows
    )

    saved_plan = runtime.state_store.load_plan("stage5-e2e")
    assert saved_plan["status"] == "COMPLETED"
    assert saved_plan["project_id"] == "stage5-project"
    assert saved_plan["context"] == {"workspace": "stage5"}

    # A new runtime process must restore the completed plan, tasks, and evidence.
    restored = AgentRuntime.create(state_path=state_path)
    restored.tool_router._confirmation = lambda *_args: False
    restored_plan, restored_graph = restored.orchestrator.restore_graph("stage5-e2e")

    assert restored_plan.status == PlanStatus.COMPLETED
    assert restored_graph.tasks["inspect"].status == TaskStatus.COMPLETED
    assert restored_graph.tasks["verify"].status == TaskStatus.COMPLETED
    for task_id, result in execution_results:
        evidence = restored.evidence_store.get(result.evidence_id)
        assert evidence is not None
        assert evidence.success is True
        assert evidence.attempt_id == f"{task_id}:attempt:1"

    resumed_plan, resumed_graph = restored.resume_goal("stage5-e2e")
    assert resumed_plan.status == PlanStatus.COMPLETED
    assert all(task.status == TaskStatus.COMPLETED for task in resumed_graph.tasks.values())
