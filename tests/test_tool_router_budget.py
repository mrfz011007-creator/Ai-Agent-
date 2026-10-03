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
