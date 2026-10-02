from core.orchestrator import Orchestrator
from core.plan import Planner, PlanProposal
from core.contracts import Task, TaskStatus, ToolResult, VerificationResult, VerificationStatus


class FakeRuntime:
    def __init__(self):
        self.calls = []

    def execute(self, proposal, *, task_id, attempt_id):
        self.calls.append((proposal.tool, task_id, attempt_id))
        return type("Result", (), {
            "success": True,
            "status": "success",
            "error": None,
            "evidence_id": f"ev-{task_id}",
        })()


def test_orchestrator_model_loop_runs_one_task_and_verifies():
    from core.task_manager import TaskManager
    from verification.acceptance import AcceptanceGate
    from core.contracts import VerificationResult, VerificationStatus

    manager = TaskManager()
    planner = Planner()
    orchestrator = Orchestrator(manager, planner)
    proposal = PlanProposal(
        goal="inspect",
        tasks=(Task("inspect", "Inspect project"),),
        acceptance_criteria=("inspection succeeds",),
    )
    _, graph = orchestrator.materialize(proposal, "plan-1")
    runtime = FakeRuntime()

    def model_call(_):
        return '{"tool":"lihat","action":"execute","arguments":{}}'

    def execute_proposal(p, *, task_id, attempt_id):
        return runtime.execute(p, task_id=task_id, attempt_id=attempt_id)

    def verify_execution(*, task_id, evidence_ids, expected_attempt_id):
        return VerificationResult(
            VerificationStatus.PASSED,
            "verified",
            tuple(evidence_ids),
            authority="acceptance_gate",
        )

    done = orchestrator.run_model_plan(
        graph,
        model_call=model_call,
        execute_proposal=execute_proposal,
        verify_execution=verify_execution,
        max_steps=1,
    )
    assert len(done) == 1
    assert done[0].status == TaskStatus.COMPLETED
    assert runtime.calls[0][0] == "lihat"


def test_model_failure_can_pause_task_for_recovery():
    from core.task_manager import TaskManager

    orchestrator = Orchestrator(TaskManager(), Planner())
    proposal = orchestrator.planner.propose(
        "model wait", [Task("wait", "Wait for model")]
    )
    _, graph = orchestrator.materialize(proposal, "plan-model-wait")
    calls = {"count": 0}

    def model_call(_):
        calls["count"] += 1
        raise RuntimeError("MODEL_CREDENTIALS_EXHAUSTED")

    def handle_failure(task_id, error):
        task = orchestrator.task_manager.get(task_id)
        task.status = TaskStatus.WAITING
        return type("Decision", (), {"action": "WAIT_FOR_MODEL"})()

    result = orchestrator.run_model_plan(
        graph,
        model_call=model_call,
        execute_proposal=lambda *args, **kwargs: None,
        verify_execution=lambda **kwargs: None,
        handle_model_failure=handle_failure,
        max_steps=1,
    )

    assert result == ()
    assert calls["count"] == 1
    assert graph.tasks["wait"].status == TaskStatus.WAITING


def test_recovered_execution_is_verified_against_new_attempt():
    from core.task_manager import TaskManager
    from core.contracts import ToolResult

    orchestrator = Orchestrator(TaskManager(), Planner())
    proposal = orchestrator.planner.propose(
        "retry", [Task("retry", "Retry command")]
    )
    _, graph = orchestrator.materialize(proposal, "plan-retry")
    attempts = []

    def model_call(_):
        return '{"tool":"lihat","action":"execute","arguments":{}}'

    def execute_proposal(p, *, task_id, attempt_id):
        attempts.append(attempt_id)
        if len(attempts) == 1:
            task = orchestrator.task_manager.get(task_id)
            task.status = TaskStatus.WAITING
            orchestrator.task_manager.retry(task_id)
        return ToolResult(True, "SUCCESS", p.tool, evidence_id="ev-retry")

    def verify_execution(*, task_id, evidence_ids, expected_attempt_id):
        assert expected_attempt_id == "retry:attempt:2"
        return VerificationResult(
            VerificationStatus.PASSED,
            "verified",
            evidence_ids,
            authority="acceptance_gate",
        )

    result = orchestrator.run_model_plan(
        graph,
        model_call=model_call,
        execute_proposal=execute_proposal,
        verify_execution=verify_execution,
        max_steps=1,
    )
    assert result[0].status == TaskStatus.COMPLETED


def test_run_goal_is_bounded_and_reports_completed_plan():
    from core.task_manager import TaskManager
    from core.model_planner import ModelPlanService

    orchestrator = Orchestrator(TaskManager(), Planner())

    class StaticPlanner:
        def propose(self, goal):
            return Planner().propose(
                goal,
                [Task("one", "Do one")],
                ["one completed"],
            )

    def model_call(_):
        return '{"tool":"lihat","action":"execute","arguments":{}}'

    def execute_proposal(p, *, task_id, attempt_id):
        return ToolResult(True, "SUCCESS", p.tool, evidence_id="ev-one")

    def verify_execution(*, task_id, evidence_ids, expected_attempt_id):
        return VerificationResult(
            VerificationStatus.PASSED,
            "verified",
            tuple(evidence_ids),
            authority="acceptance_gate",
        )

    plan, graph, done = orchestrator.run_goal(
        "bounded goal",
        plan_id="goal-1",
        plan_proposer=StaticPlanner(),
        model_call=model_call,
        execute_proposal=execute_proposal,
        verify_execution=verify_execution,
        max_steps=1,
    )
    assert plan.status.value == "COMPLETED"
    assert graph.is_complete()
    assert len(done) == 1
