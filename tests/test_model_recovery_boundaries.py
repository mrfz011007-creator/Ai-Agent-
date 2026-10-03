from core.contracts import Task, TaskStatus
from core.plan import TaskGraph
from core.runtime import AgentRuntime


def test_model_wait_can_resume_without_incrementing_attempt(tmp_path):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        Task("MODEL-WAIT", "model recovery", status=TaskStatus.READY)
    )
    runtime.task_manager.start(task.task_id)

    decision = runtime.handle_model_failure(
        task.task_id, RuntimeError("MODEL_CREDENTIALS_EXHAUSTED")
    )

    assert decision.action == "WAIT_FOR_MODEL"
    assert runtime.recovery_manager.can_resume_model_wait(task.task_id)

    resumed = runtime.task_manager.resume(task.task_id)
    assert resumed.status == TaskStatus.RUNNING
    assert resumed.attempts == 1


def test_tool_exception_with_model_marker_is_not_model_recovery(tmp_path):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        Task("MODEL-MARKER", "tool failure", status=TaskStatus.READY)
    )
    graph = TaskGraph()
    graph.add(task)

    def execute_proposal(proposal, **kwargs):
        raise RuntimeError("MODEL_CREDENTIALS_EXHAUSTED from tool")

    result = runtime.orchestrator.execute_model_step(
        graph,
        model_call=lambda prompt: '{"tool":"fake","action":"execute","arguments":{}}',
        execute_proposal=execute_proposal,
        verify_execution=lambda **kwargs: None,
        handle_model_failure=runtime.handle_model_failure,
        tool_catalog={"fake": {"description": "test tool", "idempotent": True}},
    )

    assert result is not None
    assert result.status == TaskStatus.FAILED
    assert runtime.task_manager.get(task.task_id).status == TaskStatus.FAILED

def test_resume_goal_retries_model_wait_without_replanning(tmp_path, monkeypatch):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        Task("MODEL-PLAN", "resume model task", status=TaskStatus.READY)
    )
    runtime.task_manager.start(task.task_id)
    runtime.handle_model_failure(
        task.task_id, RuntimeError("MODEL_CREDENTIALS_EXHAUSTED")
    )

    from core.plan import Plan, PlanStatus
    plan = Plan(
        plan_id="plan-model-wait",
        goal="resume model task",
        task_ids=(task.task_id,),
        status=PlanStatus.WAITING,
        acceptance_criteria=({"type": "all_tasks_completed"},),
    )
    runtime.orchestrator.persist_plan(plan)

    monkeypatch.setattr(
        runtime.model_gateway,
        "text",
        lambda prompt, **kwargs: '{"tool":"fake","action":"execute","arguments":{}}',
    )

    from core.contracts import ToolResult

    def execute(proposal, *, task_id, attempt_id=None):
        evidence_id = "ev-model-resume"
        result = ToolResult(
            True, "success", proposal.tool, data={"ok": True}, evidence_id=evidence_id
        )
        runtime.evidence_store.record(
            evidence_id=evidence_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool=proposal.tool,
            action=proposal.action,
            result=result,
        )
        return result

    monkeypatch.setattr(runtime, "execute_model_proposal", execute)

    resumed_plan, graph = runtime.resume_goal("plan-model-wait")

    assert resumed_plan.status == PlanStatus.COMPLETED
    assert graph.tasks[task.task_id].status == TaskStatus.COMPLETED
    assert graph.tasks[task.task_id].attempts == 1
