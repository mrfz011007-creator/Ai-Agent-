from core.budget import Budget, BudgetManager
from core.contracts import ToolRequest
from execution.router import ToolRouter
from security.policy import PolicyEngine
from verification.evidence import EvidenceStore


def test_missing_tool_handler_does_not_consume_tool_budget(tmp_path):
    registry = {
        "broken": {
            "permission": "safe",
            "capabilities": ["workspace.read"],
            "parameters": {"type": "object", "properties": {}},
        }
    }
    budget = BudgetManager(Budget(max_tool_calls=1))
    evidence = EvidenceStore()
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=budget,
        evidence=evidence,
    )

    result = router.execute(
        ToolRequest(tool="broken", action="execute", arguments={})
    )

    assert result.success is False
    assert result.status == "error"
    assert result.error == "Tool has no handler"
    assert budget.budget.tool_calls == 0


def test_successful_handler_consumes_tool_budget(tmp_path):
    registry = {
        "ok": {
            "permission": "safe",
            "capabilities": ["workspace.read"],
            "parameters": {"type": "object", "properties": {}},
            "func": lambda: {"success": True, "status": "SUCCESS"},
        }
    }
    budget = BudgetManager(Budget(max_tool_calls=1))
    evidence = EvidenceStore()
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=budget,
        evidence=evidence,
    )

    result = router.execute(
        ToolRequest(tool="ok", action="execute", arguments={})
    )

    assert result.success is True
    assert result.evidence_id is not None
    assert budget.budget.tool_calls == 1


def test_task_quota_not_consumed_when_handler_missing(tmp_path):
    from core.contracts import Task, TaskStatus, ToolRequest
    from core.execution_contract import ExecutionContract
    from core.state_store import StateStore
    from core.task_manager import TaskManager
    from core.checkpoint import CheckpointManager

    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task(
        "Q-handler",
        "quota handler",
        status=TaskStatus.READY,
        execution_contract=ExecutionContract(
            objective="quota handler",
            allowed_tools=("broken",),
            allowed_capabilities=("workspace.read",),
            max_tool_calls=1,
            completion_conditions=({"type": "evidence_success", "task_id": "Q-handler"},),
        ),
    ))
    manager.start(task.task_id)

    registry = {
        "broken": {
            "permission": "safe",
            "capabilities": ["workspace.read"],
            "parameters": {"type": "object", "properties": {}},
        }
    }
    budget = BudgetManager(Budget(max_tool_calls=1))
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=budget,
        evidence=EvidenceStore(store),
        task_getter=manager.get,
        task_tool_call_consumer=manager.consume_tool_call,
    )

    result = router.execute(
        ToolRequest(
            tool="broken",
            action="execute",
            task_id=task.task_id,
            attempt_id="Q-handler:attempt:1",
        )
    )

    assert result.status == "error"
    assert manager.get(task.task_id).tool_calls == 0


def test_task_quota_not_consumed_when_global_budget_exhausted(tmp_path):
    from core.contracts import Task, TaskStatus, ToolRequest
    from core.execution_contract import ExecutionContract
    from core.state_store import StateStore
    from core.task_manager import TaskManager
    from core.checkpoint import CheckpointManager

    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task(
        "Q-budget",
        "quota budget",
        status=TaskStatus.READY,
        execution_contract=ExecutionContract(
            objective="quota budget",
            allowed_tools=("ok",),
            allowed_capabilities=("workspace.read",),
            max_tool_calls=1,
            completion_conditions=({"type": "evidence_success", "task_id": "Q-budget"},),
        ),
    ))
    manager.start(task.task_id)

    registry = {
        "ok": {
            "permission": "safe",
            "capabilities": ["workspace.read"],
            "parameters": {"type": "object", "properties": {}},
            "func": lambda: {"success": True, "status": "SUCCESS"},
        }
    }
    budget = BudgetManager(Budget(max_tool_calls=0))
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=budget,
        evidence=EvidenceStore(store),
        task_getter=manager.get,
        task_tool_call_consumer=manager.consume_tool_call,
    )

    result = router.execute(
        ToolRequest(
            tool="ok",
            action="execute",
            task_id=task.task_id,
            attempt_id="Q-budget:attempt:1",
        )
    )

    assert result.status == "budget_exceeded"
    assert manager.get(task.task_id).tool_calls == 0
