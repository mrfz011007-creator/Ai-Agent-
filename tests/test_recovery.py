import pytest

from core.runtime import AgentRuntime
from core.contracts import Task, TaskStatus
from core.task_manager import TaskManager
from core.state_store import StateStore
from core.checkpoint import CheckpointManager
from core.recovery import RecoveryManager, ReconcileOutcome, FailureClass, classify_failure
from core.budget import BudgetManager
from core.contracts import Budget
from core.contracts import ToolResult
from verification.evidence import EvidenceStore



def test_tool_failure_on_terminal_task_is_idempotent(tmp_path):
    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        Task("R0", "already failed", status=TaskStatus.READY)
    )
    runtime.task_manager.start(task.task_id)
    runtime.task_manager.fail(task.task_id, "first failure")

    decision = runtime.recovery_controller.handle_tool_failure(
        task.task_id,
        status="error",
        error="second failure",
    )

    assert decision.action == "NO_ACTION"
    assert decision.status == TaskStatus.FAILED
    assert runtime.task_manager.get(task.task_id).status == TaskStatus.FAILED


def test_unknown_state_is_blocked(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R1", "reconcile"))
    task.status = TaskStatus.READY
    manager.start("R1")

    recovery = RecoveryManager(manager)
    recovery.recover_task("R1")

    result = recovery.reconcile("R1", ReconcileOutcome.UNKNOWN, "State is unknown.")
    assert result.status == TaskStatus.BLOCKED
    assert result.action == "BLOCK"


def test_safe_resume_requires_explicit_reason(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R2", "resume"))
    task.status = TaskStatus.READY
    manager.start("R2")

    recovery = RecoveryManager(manager, EvidenceStore(store))
    recovery.recover_task("R2")

    evidence = EvidenceStore(store)
    evidence.record(
        evidence_id="E-R2",
        task_id="R2",
        tool="reconcile",
        action="inspect",
        result=ToolResult(True, "SUCCESS", "reconcile"),
    )
    result = recovery.reconcile(
        "R2",
        ReconcileOutcome.SAFE_TO_RESUME,
        "Workspace state matches checkpoint.",
        evidence_ids=("E-R2",),
    )
    assert result.status == TaskStatus.RUNNING
    assert result.action == "RESUME"


def test_safe_resume_requires_evidence(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R3", "resume"))
    task.status = TaskStatus.READY
    manager.start("R3")

    evidence = EvidenceStore(store)
    recovery = RecoveryManager(manager, evidence)
    recovery.recover_task("R3")

    try:
        recovery.reconcile(
            "R3",
            ReconcileOutcome.SAFE_TO_RESUME,
            "State reconciled.",
        )
        assert False, "safe resume should require evidence"
    except ValueError as exc:
        assert "evidence" in str(exc).lower()


def test_evidence_survives_restart_and_allows_safe_resume(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    store = StateStore(db_path)
    evidence = EvidenceStore(store)
    evidence.record(
        evidence_id="E-R4",
        task_id="R4",
        tool="reconcile",
        action="inspect",
        result=ToolResult(True, "SUCCESS", "reconcile"),
    )

    restarted_store = StateStore(db_path)
    restarted_evidence = EvidenceStore(restarted_store)
    assert restarted_evidence.get("E-R4") is not None

    manager = TaskManager(
        store=restarted_store,
        checkpoints=CheckpointManager(restarted_store),
    )
    task = manager.create(Task("R4", "resume"))
    task.status = TaskStatus.READY
    manager.start("R4")

    recovery = RecoveryManager(manager, restarted_evidence)
    recovery.recover_task("R4")
    result = recovery.reconcile(
        "R4",
        ReconcileOutcome.SAFE_TO_RESUME,
        "State reconciled.",
        evidence_ids=("E-R4",),
    )
    assert result.status == TaskStatus.RUNNING


def test_invalid_evidence_cannot_resume(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R5", "resume"))
    task.status = TaskStatus.READY
    manager.start("R5")
    recovery = RecoveryManager(manager, EvidenceStore(store))
    recovery.recover_task("R5")

    try:
        recovery.reconcile(
            "R5",
            ReconcileOutcome.SAFE_TO_RETRY,
            "State reconciled.",
            evidence_ids=("missing",),
        )
        assert False, "invalid evidence should not authorize retry"
    except ValueError as exc:
        assert "evidence" in str(exc).lower()


def test_transient_failure_consumes_recovery_budget_and_retries(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R6", "retry"))
    task.status = TaskStatus.READY
    manager.start("R6")
    budget = BudgetManager(Budget(max_recovery_cycles=1))
    recovery = RecoveryManager(manager, EvidenceStore(store), budget)

    result = recovery.retry_after_failure(
        "R6", status="error", error="connection temporarily unavailable", idempotent=True
    )

    assert result.action == "RETRY"
    assert result.status == TaskStatus.RUNNING
    assert manager.get("R6").attempts == 2
    assert budget.budget.recovery_cycles == 1



def test_recovery_manager_does_not_retry_without_explicit_idempotency(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R11", "no implicit replay"))
    manager.start("R11")
    budget = BudgetManager(Budget(max_recovery_cycles=1))
    recovery = RecoveryManager(manager, EvidenceStore(store), budget)

    result = recovery.retry_after_failure(
        "R11", status="timeout", error="connection timed out"
    )

    assert result.action == "NO_RETRY"
    assert manager.get("R11").attempts == 1
    assert manager.get("R11").status == TaskStatus.RUNNING
    assert budget.budget.recovery_cycles == 0

def test_non_retryable_failure_does_not_retry(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R7", "deny"))
    task.status = TaskStatus.READY
    manager.start("R7")
    budget = BudgetManager(Budget(max_recovery_cycles=3))
    recovery = RecoveryManager(manager, EvidenceStore(store), budget)

    result = recovery.retry_after_failure(
        "R7", status="guard_denied", error="DESTRUCTIVE_COMMAND_DENIED"
    )

    assert result.action == FailureClass.NON_RETRYABLE.value
    assert result.status == TaskStatus.RUNNING
    assert budget.budget.recovery_cycles == 0


def test_recovery_budget_exhaustion_fails_task(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R8", "retry limit"))
    task.status = TaskStatus.READY
    manager.start("R8")
    budget = BudgetManager(Budget(max_recovery_cycles=0))
    recovery = RecoveryManager(manager, EvidenceStore(store), budget)

    result = recovery.retry_after_failure(
        "R8", status="timeout", error="timed out", idempotent=True
    )

    assert result.action == "BLOCK"
    assert result.status == TaskStatus.FAILED
    assert manager.get("R8").status == TaskStatus.FAILED


def test_failure_classifier_is_conservative():
    assert classify_failure("timeout", "request timed out") == FailureClass.RETRYABLE
    assert classify_failure("guard_denied", "DESTRUCTIVE_COMMAND_DENIED") == FailureClass.NON_RETRYABLE


def test_recovery_controller_fails_non_retryable_tool_error(tmp_path):
    from core.contracts import Task, TaskStatus
    from core.recovery import RecoveryController, RecoveryManager

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task("CTRL-1", "guard failure"))
    runtime.task_manager.start(task.task_id)

    controller = RecoveryController(runtime.recovery_manager, runtime.task_manager)
    decision = controller.handle_tool_failure(
        task.task_id,
        status="guard_denied",
        error="DESTRUCTIVE_COMMAND_DENIED",
    )

    assert decision.action == "FAIL"
    assert runtime.task_manager.get(task.task_id).status == TaskStatus.FAILED


def test_recovery_controller_blocks_human_required_failure(tmp_path):
    from core.contracts import Task, TaskStatus
    from core.recovery import RecoveryController

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task("CTRL-2", "budget failure"))
    runtime.task_manager.start(task.task_id)

    controller = RecoveryController(runtime.recovery_manager, runtime.task_manager)
    decision = controller.handle_tool_failure(
        task.task_id,
        status="budget_exceeded",
        error="TOOL_BUDGET_EXCEEDED",
    )

    assert decision.action == "HUMAN_REQUIRED"
    assert runtime.task_manager.get(task.task_id).status == TaskStatus.BLOCKED


def test_recovery_controller_handles_model_exhaustion(tmp_path):
    from core.contracts import Task, TaskStatus
    from core.recovery import RecoveryController

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task("CTRL-3", "model exhaustion"))
    runtime.task_manager.start(task.task_id)

    controller = RecoveryController(runtime.recovery_manager, runtime.task_manager)
    decision = controller.handle_model_failure(
        task.task_id,
        RuntimeError("MODEL_CREDENTIALS_EXHAUSTED"),
    )

    assert decision.action == "WAIT_FOR_MODEL"
    assert runtime.task_manager.get(task.task_id).status == TaskStatus.WAITING


def test_runtime_retry_creates_new_attempt_and_current_evidence_only(tmp_path):
    from execution.router import ToolRouter
    from core.contracts import ToolResult, ToolRequest
    from security.policy import PolicyEngine

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task("R9", "retry execution", status=TaskStatus.READY))
    runtime.task_manager.start("R9")

    calls = []

    def flaky_tool():
        calls.append("call")
        if len(calls) == 1:
            return {
                "success": False,
                "status": "error",
                "stderr": "connection temporarily unavailable",
            }
        return {
            "success": True,
            "status": "success",
            "stdout": "ok",
        }

    registry = {
        "flaky": {
            "func": flaky_tool,
            "permission": "safe",
            "idempotent": True,
        }
    }
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=runtime.budget_manager,
        evidence=runtime.evidence_store,
    )
    runtime.tool_router = router

    result = runtime.execute_with_recovery(
        "flaky",
        {},
        task_id="R9",
        source="test",
    )

    assert result.success
    assert len(calls) == 2
    assert runtime.task_manager.get("R9").attempts == 2

    first = runtime.evidence_store.get(result.evidence_id)
    assert first is not None
    assert first.attempt_id == "R9:attempt:2"

    evidence_rows = [
        runtime.evidence_store.get(eid)
        for eid in (
            result.evidence_id,
        )
    ]
    assert all(item.task_id == "R9" for item in evidence_rows)


def test_runtime_does_not_replay_non_idempotent_tool_after_ambiguous_failure(tmp_path):
    from execution.router import ToolRouter
    from security.policy import PolicyEngine

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task("R10", "avoid duplicate side effect", status=TaskStatus.READY))
    runtime.task_manager.start(task.task_id)
    calls = []

    def side_effect():
        calls.append("called")
        return {"success": False, "status": "error", "stderr": "connection temporarily unavailable"}

    registry = {"side_effect": {"func": side_effect, "permission": "safe"}}
    runtime.tool_router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=runtime.budget_manager,
        evidence=runtime.evidence_store,
    )

    result = runtime.execute_with_recovery("side_effect", {}, task_id=task.task_id)

    assert not result.success
    assert len(calls) == 1
    assert runtime.task_manager.get(task.task_id).attempts == 1


def test_model_failure_transition_rolls_back_on_persistence_error(tmp_path):
    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        Task("MODEL-PERSIST", "model persistence failure")
    )
    runtime.task_manager.start(task.task_id)

    def fail_save(*args, **kwargs):
        raise OSError("simulated sqlite failure")

    runtime.state_store.save_task = fail_save

    with pytest.raises(OSError):
        runtime.handle_model_failure(
            task.task_id,
            RuntimeError("MODEL_CREDENTIALS_EXHAUSTED"),
        )

    assert runtime.task_manager.get(task.task_id).status == TaskStatus.RUNNING
    saved = StateStore(tmp_path / "state.sqlite3").load_task(task.task_id)
    assert saved["status"] == TaskStatus.RUNNING


def test_safe_reconciliation_rejects_successful_evidence_from_another_task(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    manager.create(Task("R12", "target"))
    manager.start("R12")

    evidence = EvidenceStore(store)
    evidence.record(
        evidence_id="E-R12-OTHER",
        task_id="OTHER-TASK",
        tool="reconcile",
        action="inspect",
        result=ToolResult(True, "SUCCESS", "reconcile"),
    )

    recovery = RecoveryManager(manager, evidence)
    recovery.recover_task("R12")

    with pytest.raises(ValueError, match="another task"):
        recovery.reconcile(
            "R12",
            ReconcileOutcome.SAFE_TO_RESUME,
            "State appears reconciled.",
            evidence_ids=("E-R12-OTHER",),
        )


def test_safe_reconciliation_retry_respects_contract_limit(tmp_path):
    from core.execution_contract import ExecutionContract

    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    contract = ExecutionContract(
        objective="bounded retry",
        allowed_tools=("tool",),
        allowed_capabilities=("workspace.read",),
        max_tool_calls=3,
        retry_limit=0,
        evidence_required=True,
        completion_conditions=("verified",),
    )
    manager.create(Task("R13", "bounded retry", execution_contract=contract))
    manager.start("R13")

    evidence = EvidenceStore(store)
    evidence.record(
        evidence_id="E-R13",
        task_id="R13",
        tool="reconcile",
        action="inspect",
        result=ToolResult(True, "SUCCESS", "reconcile"),
    )

    recovery = RecoveryManager(
        manager,
        evidence,
        BudgetManager(Budget(max_recovery_cycles=1)),
    )
    recovery.recover_task("R13")

    with pytest.raises(ValueError, match="retry limit"):
        recovery.reconcile(
            "R13",
            ReconcileOutcome.SAFE_TO_RETRY,
            "Retry was inspected and considered safe.",
            evidence_ids=("E-R13",),
        )

    assert manager.get("R13").status == TaskStatus.WAITING


def test_safe_reconciliation_retry_consumes_recovery_budget(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    manager.create(Task("R14", "bounded reconciliation retry"))
    manager.start("R14")

    evidence = EvidenceStore(store)
    evidence.record(
        evidence_id="E-R14",
        task_id="R14",
        tool="reconcile",
        action="inspect",
        result=ToolResult(True, "SUCCESS", "reconcile"),
    )

    budget = BudgetManager(Budget(max_recovery_cycles=1))
    recovery = RecoveryManager(manager, evidence, budget)
    recovery.recover_task("R14")

    result = recovery.reconcile(
        "R14",
        ReconcileOutcome.SAFE_TO_RETRY,
        "Retry explicitly authorized after inspection.",
        evidence_ids=("E-R14",),
    )

    assert result.action == "RETRY"
    assert result.status == TaskStatus.RUNNING
    assert budget.budget.recovery_cycles == 1
