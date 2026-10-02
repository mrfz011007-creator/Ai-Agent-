from core.contracts import Task, VerificationResult, VerificationStatus
from core.orchestrator import Orchestrator
from core.plan import Planner


def _orchestrator():
    from core.task_manager import TaskManager
    return Orchestrator(TaskManager(), Planner())


def test_execute_step_completes_and_unlocks_dependency():
    o = _orchestrator()
    proposal = o.planner.propose(
        "pipeline",
        [Task("inspect", "inspect"), Task("build", "build", dependencies=["inspect"])],
    )
    _, graph = o.materialize(proposal, "plan-loop")
    seen = []

    def execute(task):
        seen.append(("execute", task.task_id))
        return VerificationResult(VerificationStatus.PASSED, "executed", (f"e-{task.task_id}",), authority="acceptance_gate")

    first = o.execute_step(
        graph,
        execute=execute,
        verify=lambda task, verification: verification,
    )
    assert first.status.value == "COMPLETED"
    assert seen == [("execute", "inspect")]
    assert o.next_ready(graph).task_id == "build"


def test_execute_step_fails_closed_on_execution_exception():
    o = _orchestrator()
    proposal = o.planner.propose("pipeline", [Task("a", "a")])
    _, graph = o.materialize(proposal, "plan-fail")

    result = o.execute_step(
        graph,
        execute=lambda _: (_ for _ in ()).throw(RuntimeError("tool crashed")),
        verify=lambda *_: None,
    )
    assert result.status.value == "FAILED"


def test_run_until_blocked_respects_step_budget():
    o = _orchestrator()
    proposal = o.planner.propose(
        "pipeline",
        [Task("a", "a"), Task("b", "b", dependencies=["a"])],
    )
    _, graph = o.materialize(proposal, "plan-budget")
    result = o.run_until_blocked(
        graph,
        execute=lambda task: VerificationResult(VerificationStatus.PASSED, "ok", (f"e-{task.task_id}",), authority="acceptance_gate"),
        verify=lambda task, verification: verification,
        max_steps=1,
    )
    assert [task.task_id for task in result] == ["a"]
    assert graph.tasks["b"].status.value == "PENDING"
