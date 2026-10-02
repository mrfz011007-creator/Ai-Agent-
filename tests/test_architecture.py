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
