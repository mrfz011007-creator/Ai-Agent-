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
        return '{"goal":"build app","tasks":[{"task_id":"build","title":"Build app","dependencies":[]}],"acceptance_criteria":["build succeeds"]}'

    monkeypatch.setattr(runtime.model_gateway, "generate_text", fake_generate_text)

    plan, graph = runtime.plan_goal("build app", plan_id="plan-model")

    assert plan.plan_id == "plan-model"
    assert graph.tasks["build"].title == "Build app"
    assert calls
    assert "build app" in calls[0]


def test_runtime_budget_survives_restart(tmp_path):
    state_path = tmp_path / "state.sqlite3"
    first = AgentRuntime.create(state_path=state_path)
    first.budget_manager.budget.max_tool_calls = 3
    first.budget_manager.budget.max_model_calls = 3
    first.budget_manager.budget.max_recovery_cycles = 3

    first.budget_manager.reserve_tool_call()
    first.budget_manager.reserve_model_call()
    first.budget_manager.reserve_recovery_cycle()

    second = AgentRuntime.create(state_path=state_path)

    assert second.budget_manager.budget.tool_calls == 1
    assert second.budget_manager.budget.model_calls == 1
    assert second.budget_manager.budget.recovery_cycles == 1
    assert second.budget_manager.remaining_tool_calls == 2
    assert second.budget_manager.remaining_model_calls == 2
    assert second.budget_manager.remaining_recovery_cycles == 2


def test_runtime_budget_window_resets_after_restart(tmp_path):
    state_path = tmp_path / "state.sqlite3"
    first = AgentRuntime.create(state_path=state_path)
    first.budget_manager.budget.max_runtime_seconds = 1.0
    first.budget_manager.started_at_wall = 0.0
    first.budget_manager._persist()

    second = AgentRuntime.create(state_path=state_path)

    assert second.budget_manager.elapsed_seconds < 1.0
    assert second.budget_manager.remaining_runtime_seconds > 0.0
