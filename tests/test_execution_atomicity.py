from __future__ import annotations

from core.budget import BudgetManager
from core.contracts import Budget, Task, TaskStatus, ToolRequest
from core.state_store import StateStore
from core.task_manager import TaskManager
from execution.router import ToolRouter
from security.policy import PolicyEngine
from verification.evidence import EvidenceStore


def _router(tmp_path, registry, manager=None, budget=None):
    store = StateStore(tmp_path / "state.sqlite3")
    evidence = EvidenceStore(store)
    budget = budget or BudgetManager(Budget(max_tool_calls=10))
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=budget,
        evidence=evidence,
        task_getter=manager.get if manager else None,
        task_tool_call_consumer=manager.consume_tool_call if manager else None,
        task_tool_call_refunder=manager.refund_tool_call if manager else None,
    )
    return router, evidence, store, budget


def test_rejected_request_does_not_consume_task_or_global_budget(tmp_path):
    task_manager = TaskManager()
    task = task_manager.create(Task("B1", "budget ordering"))
    task.status = TaskStatus.RUNNING
    registry = {
        "tool": {
            "func": lambda required: {"success": True},
            "permission": "safe",
            "parameters": {
                "type": "object",
                "properties": {"required": {"type": "string"}},
                "required": ["required"],
            },
        }
    }
    router, _, _, budget = _router(tmp_path, registry, task_manager)

    result = router.execute(
        ToolRequest("tool", "execute", arguments={}, task_id=task.task_id)
    )

    assert result.status == "schema_invalid"
    assert task.tool_calls == 0
    assert budget.budget.tool_calls == 0


def test_global_budget_is_refunded_when_task_persistence_fails(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store)
    task = manager.create(Task("B2", "compensate quota"))

    original_save = store.save_task

    def fail_save(*args, **kwargs):
        raise OSError("simulated sqlite failure")

    store.save_task = fail_save
    evidence = EvidenceStore(store)
    budget = BudgetManager(Budget(max_tool_calls=1))
    router = ToolRouter(
        registry_getter=lambda name: {
            "func": lambda: {"success": True},
            "permission": "safe",
        } if name == "tool" else None,
        policy=PolicyEngine(lambda name: {
            "func": lambda: {"success": True},
            "permission": "safe",
        } if name == "tool" else None),
        budget=budget,
        evidence=evidence,
        task_getter=manager.get,
        task_tool_call_consumer=manager.consume_tool_call,
        task_tool_call_refunder=manager.refund_tool_call,
    )

    try:
        result = router.execute(
            ToolRequest("tool", "execute", task_id=task.task_id)
        )
    finally:
        store.save_task = original_save

    assert not result.success
    assert budget.budget.tool_calls == 0
    assert task.tool_calls == 0


def test_completed_execution_is_not_replayed(tmp_path):
    calls = []
    registry = {
        "tool": {
            "func": lambda: calls.append("called") or {"success": True, "status": "success", "value": 7},
            "permission": "safe",
        }
    }
    router, evidence, store, budget = _router(tmp_path, registry)
    request = ToolRequest("tool", "execute", request_id="REQ-CACHED")

    first = router.execute(request)
    second = router.execute(request)

    assert first.success and second.success
    assert first.evidence_id == second.evidence_id
    assert len(calls) == 1
    assert budget.budget.tool_calls == 1
    assert store.load_tool_execution("REQ-CACHED")["status"] == "COMPLETED"
    assert evidence.get(first.evidence_id) is not None


def test_evidence_failure_marks_execution_unknown_and_blocks_retry(tmp_path):
    calls = []
    registry = {
        "side_effect": {
            "func": lambda: calls.append("called") or {"success": True, "status": "success"},
            "permission": "safe",
        }
    }
    router, _, store, _ = _router(tmp_path, registry)

    def fail_evidence(*args, **kwargs):
        raise OSError("evidence store unavailable")

    store.save_evidence = fail_evidence

    first = router.execute(
        ToolRequest("side_effect", "execute", task_id="T-UNKNOWN", request_id="REQ-UNKNOWN")
    )

    assert not first.success
    assert first.status == "execution_unknown"
    assert len(calls) == 1
    assert store.load_tool_execution("REQ-UNKNOWN")["status"] == "UNKNOWN"

    second = router.execute(
        ToolRequest("side_effect", "execute", task_id="T-UNKNOWN", request_id="REQ-NEW")
    )

    assert not second.success
    assert second.status == "execution_reconciliation_required"
    assert len(calls) == 1


def test_uncertain_execution_blocks_replay_after_restart(tmp_path):
    db = tmp_path / "state.sqlite3"
    calls = []
    registry = {
        "side_effect": {
            "func": lambda: calls.append("called") or {"success": True},
            "permission": "safe",
        }
    }

    store = StateStore(db)
    evidence = EvidenceStore(store)
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=evidence,
    )
    store.save_evidence = lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("evidence unavailable")
    )

    first = router.execute(
        ToolRequest(
            "side_effect",
            "execute",
            task_id="T-RESTART",
            request_id="REQ-RESTART",
        )
    )
    assert first.status == "execution_unknown"

    restarted_store = StateStore(db)
    restarted = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=EvidenceStore(restarted_store),
    )
    second = restarted.execute(
        ToolRequest(
            "side_effect",
            "execute",
            task_id="T-RESTART",
            request_id="REQ-NEW",
        )
    )

    assert second.status == "execution_reconciliation_required"
    assert len(calls) == 1
