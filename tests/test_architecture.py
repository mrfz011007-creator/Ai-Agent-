from core.budget import BudgetManager
from core.contracts import (
    Budget,
    Task,
    TaskStatus,
    ToolRequest,
    VerificationResult,
    VerificationStatus,
)
from core.task_manager import TaskManager
from security.policy import PolicyEngine
from execution.router import ToolRouter
from verification.evidence import EvidenceStore
from verification.verifier import Verifier


REGISTRY = {
    "safe_tool": {
        "permission": "safe",
        "func": lambda: {"ok": True},
    },
    "confirm_tool": {
        "permission": "confirm",
        "func": lambda: {"ok": True},
    },
    "blocked_tool": {
        "permission": "blocked",
        "func": lambda: {"ok": True},
    },
}


def make_router(max_calls=5, confirmation=None):
    evidence = EvidenceStore()
    budget = BudgetManager(Budget(max_tool_calls=max_calls))
    policy = PolicyEngine(REGISTRY.get)
    router = ToolRouter(
        registry_getter=REGISTRY.get,
        policy=policy,
        budget=budget,
        evidence=evidence,
        confirmation=confirmation,
    )
    return router, evidence, budget


def test_unknown_tool_is_denied():
    router, _, _ = make_router()
    result = router.execute(ToolRequest("missing", "execute"))
    assert result.status == "denied"


def test_blocked_tool_is_denied():
    router, _, _ = make_router()
    result = router.execute(ToolRequest("blocked_tool", "execute"))
    assert result.status == "denied"


def test_confirm_tool_requires_approval():
    router, _, _ = make_router(confirmation=lambda *_: False)
    result = router.execute(ToolRequest("confirm_tool", "execute"))
    assert result.status == "cancelled"


def test_tool_execution_creates_evidence_and_can_be_verified():
    router, evidence, _ = make_router()
    result = router.execute(
        ToolRequest("safe_tool", "execute", task_id="T1")
    )
    assert result.success is True
    assert result.evidence_id in evidence.records

    verification = Verifier(evidence).verify_evidence(result.evidence_id)
    assert verification.status == VerificationStatus.PASSED


def test_model_and_recovery_budgets_are_hard():
    budget = BudgetManager(Budget(max_model_calls=1, max_recovery_cycles=1))
    budget.reserve_model_call()
    try:
        budget.reserve_model_call()
        assert False, "Model budget must be hard"
    except RuntimeError as error:
        assert str(error) == "MODEL_BUDGET_EXCEEDED"

    budget.reserve_recovery_cycle()
    try:
        budget.reserve_recovery_cycle()
        assert False, "Recovery budget must be hard"
    except RuntimeError as error:
        assert str(error) == "RECOVERY_BUDGET_EXCEEDED"


def test_budget_snapshot_exposes_all_runtime_limits():
    budget = BudgetManager(
        Budget(max_tool_calls=2, max_model_calls=3, max_recovery_cycles=4)
    )
    snapshot = budget.snapshot()
    assert snapshot["remaining_tool_calls"] == 2
    assert snapshot["remaining_model_calls"] == 3
    assert snapshot["remaining_recovery_cycles"] == 4
    assert "remaining_runtime_seconds" in snapshot


def test_output_budget_truncates_large_tool_output():
    registry = {"safe_tool": {"permission": "safe", "func": lambda: "x" * 20}}
    evidence = EvidenceStore()
    budget = BudgetManager(Budget(max_output_chars=10))
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=budget,
        evidence=evidence,
    )
    result = router.execute(ToolRequest("safe_tool", "execute"))
    assert result.success is True
    assert result.data == "x" * 10
    assert result.error == "OUTPUT_TRUNCATED"


def test_budget_is_hard():
    router, _, budget = make_router(max_calls=1)
    assert router.execute(ToolRequest("safe_tool", "execute")).success
    second = router.execute(ToolRequest("safe_tool", "execute"))
    assert second.status == "budget_exceeded"
    assert budget.remaining_tool_calls == 0


def test_completed_requires_verification():
    manager = TaskManager()
    task = manager.create(Task("T1", "test"))
    task.status = TaskStatus.READY
    manager.start("T1")
    manager.begin_verification("T1")

    failed = VerificationResult(
        status=VerificationStatus.FAILED,
        reason="not proven",
    )

    try:
        manager.complete("T1", failed)
        assert False, "Completion without PASS must fail"
    except ValueError:
        pass


def test_completed_requires_evidence_id():
    manager = TaskManager()
    task = manager.create(Task("T6", "evidence gate"))
    task.status = TaskStatus.READY
    manager.start("T6")
    manager.begin_verification("T6")
    passed_without_evidence = VerificationResult(
        status=VerificationStatus.PASSED,
        reason="claimed success",
    )
    try:
        manager.complete("T6", passed_without_evidence)
        assert False, "Completion without evidence must fail"
    except ValueError as error:
        assert "evidence" in str(error).lower()


def test_verification_can_load_evidence_after_restart(tmp_path):
    from core.state_store import StateStore

    db = tmp_path / "verification.sqlite3"
    first_store = StateStore(db)
    first_evidence = EvidenceStore(first_store)
    evidence_id = "E-V1"
    first_evidence.record(
        evidence_id=evidence_id,
        task_id="V1",
        tool="build",
        action="assemble",
        result=__import__("core.contracts", fromlist=["ToolResult"]).ToolResult(
            True, "SUCCESS", "build"
        ),
    )

    restarted = EvidenceStore(StateStore(db))
    verification = Verifier(restarted).verify_evidence(evidence_id)
    assert verification.status == VerificationStatus.PASSED
    assert verification.evidence_ids == (evidence_id,)


def test_state_store_persists_task_and_checkpoint(tmp_path):
    from core.state_store import StateStore
    from core.checkpoint import CheckpointManager

    store = StateStore(tmp_path / "state.sqlite3")
    store.save_task("T1", "RUNNING", 1, {"title": "build"})
    assert store.load_task("T1")["payload"]["title"] == "build"

    manager = CheckpointManager(store)
    task = Task("T1", "build", status=TaskStatus.RUNNING, attempts=1)
    checkpoint = manager.capture(task, workspace="demo")
    latest = manager.latest("T1")
    assert latest["checkpoint_id"] == checkpoint.checkpoint_id
    assert latest["payload"]["workspace"] == "demo"


def test_task_manager_persists_transitions_and_checkpoints(tmp_path):
    from core.state_store import StateStore
    from core.checkpoint import CheckpointManager

    store = StateStore(tmp_path / "state.sqlite3")
    checkpoints = CheckpointManager(store)
    manager = TaskManager(store=store, checkpoints=checkpoints)
    manager.create(Task("T2", "persisted"))
    task = manager.get("T2")
    task.status = TaskStatus.READY
    manager.start("T2")

    saved = store.load_task("T2")
    assert saved["status"] == "RUNNING"
    assert saved["attempts"] == 1
    assert checkpoints.latest("T2") is not None


def test_task_manager_restores_persisted_task_after_restart(tmp_path):
    from core.state_store import StateStore
    from core.checkpoint import CheckpointManager

    db = tmp_path / "restart.sqlite3"
    first = TaskManager(
        store=StateStore(db),
        checkpoints=CheckpointManager(StateStore(db)),
    )
    task = first.create(Task("T3", "restart me"))
    task.status = TaskStatus.READY
    first.start("T3")

    second = TaskManager(
        store=StateStore(db),
        checkpoints=CheckpointManager(StateStore(db)),
    )
    restored = second.restore("T3")
    assert restored is not None
    assert restored.status == TaskStatus.RUNNING
    assert restored.attempts == 1
    assert restored.title == "restart me"


def test_runtime_factory_does_not_create_state_until_requested(tmp_path):
    from core.runtime import AgentRuntime

    runtime = AgentRuntime.create(tmp_path / "runtime.sqlite3")
    assert runtime.state_store.path.exists()
    assert runtime.task_manager.store is runtime.state_store


def test_recovery_moves_interrupted_task_to_waiting(tmp_path):
    from core.state_store import StateStore
    from core.checkpoint import CheckpointManager
    from core.recovery import RecoveryManager

    store = StateStore(tmp_path / "recovery.sqlite3")
    manager = TaskManager(
        store=store,
        checkpoints=CheckpointManager(store),
    )
    task = manager.create(Task("T4", "interrupted"))
    task.status = TaskStatus.READY
    manager.start("T4")

    recovery = RecoveryManager(manager)
    decision = recovery.recover_task("T4")

    assert decision.status == TaskStatus.WAITING
    assert decision.action == "WAIT_FOR_RECONCILIATION"
    assert manager.get("T4").status == TaskStatus.WAITING
    assert manager.checkpoints.latest("T4")["payload"]["event"] == "recovery_required"


def test_recovery_does_not_change_completed_task(tmp_path):
    from core.state_store import StateStore
    from core.checkpoint import CheckpointManager
    from core.recovery import RecoveryManager

    store = StateStore(tmp_path / "recovery.sqlite3")
    manager = TaskManager(
        store=store,
        checkpoints=CheckpointManager(store),
    )
    task = manager.create(Task("T5", "done"))
    task.status = TaskStatus.COMPLETED

    decision = RecoveryManager(manager).recover_task("T5")

    assert decision.action == "NO_ACTION"
    assert decision.status == TaskStatus.COMPLETED


def test_runtime_exposes_acceptance_gate(tmp_path):
    from core.runtime import AgentRuntime

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    assert runtime.acceptance_gate is not None


def test_runtime_exposes_build_and_test_managers(tmp_path):
    from core.runtime import AgentRuntime

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    assert runtime.artifact_manager is not None
    assert runtime.build_manager is not None
    assert runtime.test_manager is not None


def test_workspace_guard_denies_command_outside_workspace(tmp_path):
    from execution.router import ToolRouter
    from security.guard import GuardEngine
    from core.budget import BudgetManager
    from core.contracts import Budget, ToolRequest
    from verification.evidence import EvidenceStore
    from security.policy import PolicyEngine

    registry = {
        "run_command": {
            "func": lambda **kwargs: {"success": True},
            "permission": "safe",
        }
    }
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=EvidenceStore(),
        guard=GuardEngine(tmp_path),
    )
    result = router.execute(ToolRequest(
        tool="run_command",
        action="build",
        arguments={"command": "python -c \"print('x')\"", "cwd": str(tmp_path.parent)},
        task_id="GUARD-1",
    ))
    assert not result.success
    assert result.status == "guard_denied"
    assert result.error == "WORKSPACE_BOUNDARY_VIOLATION"


def test_command_runner_redacts_secrets_and_caps_output(tmp_path):
    from execution.command import run_command

    result = run_command(
        command="python -c \"print('api_key=TOPSECRET ' + 'x'*200)\"",
        cwd=str(tmp_path),
        max_output_chars=50,
    )
    assert result["success"]
    assert "TOPSECRET" not in result["stdout"]
    assert "***REDACTED***" in result["stdout"]
    assert result["output_truncated"]


def test_command_runner_reports_timeout(tmp_path):
    from execution.command import run_command

    result = run_command(
        command="python -c \"import time; time.sleep(2)\"",
        cwd=str(tmp_path),
        timeout=0.1,
    )
    assert not result["success"]
    assert result["status"] == "TIMEOUT"


def test_workspace_guard_denies_destructive_commands(tmp_path):
    from security.guard import GuardEngine

    guard = GuardEngine(tmp_path)
    result = guard.check(
        tool="run_command",
        arguments={"command": "rm -rf project", "cwd": str(tmp_path)},
    )
    assert not result.allowed
    assert result.reason == "DESTRUCTIVE_COMMAND_DENIED"


def test_tool_router_redacts_secrets_and_limits_structured_output(tmp_path):
    from core.budget import BudgetManager
    from core.contracts import Budget, ToolRequest
    from execution.router import ToolRouter
    from security.guard import GuardEngine
    from security.policy import PolicyEngine
    from verification.evidence import EvidenceStore

    secret = "AIza1234567890123456789012345"
    registry = {
        "run_command": {
            "func": lambda **kwargs: {
                "success": True,
                "status": "SUCCESS",
                "stdout": secret + ("x" * 200),
                "stderr": "Bearer SUPERSECRET123456789",
            },
            "permission": "safe",
        }
    }
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget(max_output_chars=50)),
        evidence=EvidenceStore(),
        guard=GuardEngine(tmp_path),
    )
    result = router.execute(
        ToolRequest(
            tool="run_command",
            action="test",
            arguments={"command": "echo ok", "cwd": str(tmp_path)},
            task_id="RED-1",
        )
    )
    assert result.success
    assert "[REDACTED_SECRET]" in result.data["stdout"]
    assert len(result.data["stdout"]) <= 50
    assert "[REDACTED_SECRET]" in result.data["stderr"]


def test_runtime_tool_execution_completion(tmp_path):
    from core.contracts import Task, TaskStatus
    from core.runtime import AgentRuntime

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        Task(task_id="task-e2e", title="evidence-backed tool execution")
    )
    runtime.task_manager.start(task.task_id)

    result = runtime.execute_with_recovery(
        "search_memory",
        {"query": "e2e"},
        source="test",
        task_id=task.task_id,
    )

    assert result.success
    evidence = runtime.evidence_store.get(result.evidence_id)
    assert evidence.attempt_id == "task-e2e:attempt:1"
    completed = runtime.verify_tool_execution(
        task.task_id, [result.evidence_id]
    )
    assert completed.status == TaskStatus.COMPLETED
