from core.orchestrator import Orchestrator
from core.plan import Planner, PlanProposal
from core.contracts import Task, TaskStatus


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
