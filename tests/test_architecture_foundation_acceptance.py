from core.budget import BudgetManager
from core.contracts import Budget, Task, TaskStatus, ToolRequest
from execution.router import ToolRouter
from security.policy import PolicyEngine
from verification.evidence import EvidenceStore


def _router(registry):
    return ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=EvidenceStore(),
    )


def test_router_rejects_missing_required_argument_before_execution():
    calls = []

    def tool(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    registry = {
        "write": {
            "func": tool,
            "permission": "safe",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    }

    result = _router(registry).execute(
        ToolRequest("write", "execute", arguments={})
    )

    assert not result.success
    assert result.status == "schema_invalid"
    assert "MISSING_REQUIRED_ARGUMENT" in result.error
    assert calls == []


def test_router_rejects_wrong_argument_type_before_execution():
    calls = []

    def tool(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    registry = {
        "read": {
            "func": tool,
            "permission": "safe",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    }

    result = _router(registry).execute(
        ToolRequest("read", "execute", arguments={"path": 123})
    )

    assert not result.success
    assert result.status == "schema_invalid"
    assert "INVALID_ARGUMENT_TYPE" in result.error
    assert calls == []


def test_router_executes_valid_schema_and_records_evidence():
    registry = {
        "read": {
            "func": lambda path: {"success": True, "path": path},
            "permission": "safe",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    }
    evidence = EvidenceStore()
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=evidence,
    )

    result = router.execute(
        ToolRequest("read", "execute", {"path": "README.md"}, task_id="schema-1")
    )

    assert result.success
    assert result.evidence_id is not None
    assert evidence.get(result.evidence_id).task_id == "schema-1"


def test_goal_lifecycle_completes_through_runtime_and_survives_restart(tmp_path):
    from core.runtime import AgentRuntime

    db = tmp_path / "state.sqlite3"
    runtime = AgentRuntime.create(db)
    task = runtime.task_manager.create(
        Task("E2E-1", "bounded goal execution", status=TaskStatus.READY, execution_contract=ExecutionContract(objective="bounded goal execution", allowed_tools=("search_memory",), allowed_capabilities=("workspace.read",), completion_conditions=({"type":"evidence_success","task_id":"E2E-1"},)))
    )
    runtime.task_manager.start(task.task_id)

    result = runtime.execute_with_recovery(
        "search_memory",
        {"query": "bounded"},
        source="model",
        task_id=task.task_id,
    )
    assert result.success
    assert result.evidence_id is not None

    completed = runtime.verify_tool_execution(task.task_id, [result.evidence_id])
    assert completed.status == TaskStatus.COMPLETED

    restarted = AgentRuntime.create(db)
    restored = restarted.task_manager.restore(task.task_id)
    assert restored is not None
    assert restored.status == TaskStatus.COMPLETED
    assert restored.result.authority == "acceptance_gate"
    assert restored.result.status.value == "PASSED"
    assert result.evidence_id in restored.result.evidence_ids
