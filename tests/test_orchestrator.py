from core.contracts import Task, TaskStatus, VerificationResult, VerificationStatus
from core.orchestrator import Orchestrator
from core.plan import Planner


def test_orchestrator_materializes_and_runs_one_ready_task():
    from core.task_manager import TaskManager

    manager = TaskManager()
    orchestrator = Orchestrator(manager, Planner())
    proposal = orchestrator.planner.propose(
        "build app",
        [
            Task("inspect", "inspect"),
            Task("build", "build", dependencies=["inspect"]),
        ],
    )
    plan, graph = orchestrator.materialize(proposal, "plan-1")

    assert plan.status.value == "VALIDATED"
    task = orchestrator.start_next(graph)
    assert task is not None
    assert task.task_id == "inspect"
    assert task.status == TaskStatus.RUNNING


def test_orchestrator_does_not_start_dependent_task_early():
    from core.task_manager import TaskManager

    manager = TaskManager()
    orchestrator = Orchestrator(manager, Planner())
    proposal = orchestrator.planner.propose(
        "build app",
        [Task("inspect", "inspect"), Task("build", "build", dependencies=["inspect"])],
    )
    _, graph = orchestrator.materialize(proposal, "plan-2")

    first = orchestrator.start_next(graph)
    assert first.task_id == "inspect"
    try:
        orchestrator.next_ready(graph)
        assert False, "scheduler must reject a second active task"
    except RuntimeError as error:
        assert "Active task" in str(error)
    assert orchestrator.pending_dependencies(graph, "build") == ("inspect",)


def test_orchestrator_requires_verified_completion():
    from core.task_manager import TaskManager

    manager = TaskManager()
    orchestrator = Orchestrator(manager, Planner())
    proposal = orchestrator.planner.propose("one task", [Task("a", "a")])
    _, graph = orchestrator.materialize(proposal, "plan-3")
    task = orchestrator.start_next(graph)
    manager.begin_verification(task.task_id)

    try:
        orchestrator.complete_task(
            graph,
            task.task_id,
            VerificationResult(VerificationStatus.FAILED, "not proven", ("e1",)),
        )
        assert False, "failed verification must not complete task"
    except ValueError:
        pass



def test_orchestrator_passes_working_rules_to_execution_service(monkeypatch):
    import core.orchestrator as orchestrator_module
    from core.task_manager import TaskManager

    captured = {}

    class StubExecutionService:
        def __init__(self, model_call, tool_catalog=None, *, agent_rules=None):
            captured["agent_rules"] = agent_rules

        def propose(self, task_title, context=None):
            raise RuntimeError("intentional test stop")

    monkeypatch.setattr(
        orchestrator_module, "agent_rules_prompt_section", lambda: "TEST WORKING RULES"
    )
    monkeypatch.setattr(
        orchestrator_module, "ModelExecutionService", StubExecutionService
    )

    manager = TaskManager()
    orchestrator = Orchestrator(manager, Planner())
    proposal = orchestrator.planner.propose(
        "inspect", [Task("inspect-rules", "Inspect")]
    )
    _, graph = orchestrator.materialize(proposal, "plan-rules")

    orchestrator.execute_model_step(
        graph,
        model_call=lambda _prompt: "",
        execute_proposal=lambda *_args, **_kwargs: None,
        verify_execution=lambda **_kwargs: None,
    )

    assert captured["agent_rules"] == "TEST WORKING RULES"
