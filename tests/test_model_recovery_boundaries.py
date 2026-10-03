from core.contracts import TaskStatus, ToolResult
from core.runtime import AgentRuntime


def test_model_wait_can_resume_without_incrementing_attempt(tmp_path):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        __import__("core.contracts", fromlist=["Task"]).Task(
            "MODEL-WAIT", "model recovery", status=TaskStatus.READY
        )
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
        __import__("core.contracts", fromlist=["Task"]).Task(
            "MODEL-MARKER", "tool failure", status=TaskStatus.READY
        )
    )
    runtime.task_manager.start(task.task_id)

    class Proposal:
        tool = "fake"
        action = "execute"

    result = runtime.orchestrator.execute_model_step(
        runtime.orchestrator.restore_graph if False else __import__("core.plan", fromlist=["TaskGraph"]).TaskGraph(),
        model_call=lambda prompt: '{"tool":"fake","action":"execute","arguments":{}}',
        execute_proposal=lambda proposal, **kwargs: (_ for _ in ()).throw(
            RuntimeError("MODEL_CREDENTIALS_EXHAUSTED from tool")
        ),
        verify_execution=lambda **kwargs: None,
    )

    assert result is None
