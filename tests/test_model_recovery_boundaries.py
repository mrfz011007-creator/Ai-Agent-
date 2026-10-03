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
    )

    assert result is not None
    assert result.status == TaskStatus.FAILED
    assert runtime.task_manager.get(task.task_id).status == TaskStatus.FAILED
