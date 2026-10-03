from core.contracts import Task, TaskStatus
from core.plan import Planner
from core.runtime import AgentRuntime


def test_runtime_resume_plan_reconciles_interrupted_task(tmp_path):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    proposal = Planner().propose("resume", [Task("one", "Do one")])
    plan, _ = runtime.orchestrator.materialize(proposal, "plan-resume")
    runtime.task_manager.start("one")

    restored_plan, graph = runtime.resume_plan(plan.plan_id)

    assert restored_plan.plan_id == plan.plan_id
    assert graph.tasks["one"].status == TaskStatus.WAITING
    checkpoint = runtime.state_store.load_latest_checkpoint("one")
    assert checkpoint is not None
    assert checkpoint["payload"]["event"] == "recovery_required"


def test_runtime_plan_goal_uses_model_gateway_boundary(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")

    calls = []

    def fake_generate_text(prompt, **kwargs):
        calls.append(prompt)
        return '{"goal":"build app","tasks":[{"task_id":"build","title":"Build app","dependencies":[]}],"acceptance_criteria":[{"type":"all_tasks_completed"}]}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)

    plan, graph = runtime.plan_goal("build app", plan_id="plan-model")

    assert plan.plan_id == "plan-model"
    assert graph.tasks["build"].title == "Build app"
    assert calls
    assert "build app" in calls[0]


def test_router_bounds_omitted_command_timeout_to_remaining_runtime(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    runtime.tool_router._confirmation = lambda tool, args: True
    runtime.budget_manager.budget.max_runtime_seconds = 5.0
    runtime.budget_manager.started_at = time.monotonic()

    result = runtime.tool_router.execute(
        __import__("core.contracts", fromlist=["ToolRequest"]).ToolRequest(
            tool="run_command",
            action="execute",
            arguments={
                "command": 'python3 -c "import time; time.sleep(10)"',
                "cwd": str(tmp_path),
            },
            source="test",
        )
    )

    assert result.status == "timeout"
    assert result.data["status"] == "TIMEOUT"


def test_budget_counters_persist_across_runtime_restart(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    runtime = AgentRuntime.create(state_path=db_path)
    runtime.budget_manager.budget.max_tool_calls = 3
    runtime.budget_manager.budget.max_model_calls = 3
    runtime.budget_manager.budget.max_recovery_cycles = 3

    runtime.budget_manager.reserve_tool_call()
    runtime.budget_manager.reserve_model_call()
    runtime.budget_manager.reserve_recovery_cycle()

    restarted = AgentRuntime.create(state_path=db_path)

    assert restarted.budget_manager.budget.tool_calls == 1
    assert restarted.budget_manager.budget.model_calls == 1
    assert restarted.budget_manager.budget.recovery_cycles == 1
    assert restarted.budget_manager.remaining_tool_calls == 2
    assert restarted.budget_manager.remaining_model_calls == 2
    assert restarted.budget_manager.remaining_recovery_cycles == 2
