from __future__ import annotations

from core.budget import Budget, BudgetManager
from core.contracts import ToolRequest
from execution.router import ToolRouter
from security.policy import PolicyEngine
from verification.evidence import EvidenceStore


def test_router_keeps_code_and_message_from_failed_workspace_tool():
    registry = {
        "workspace_failure": {
            "permission": "safe",
            "capabilities": ["workspace.read"],
            "parameters": {"type": "object", "properties": {}},
            "func": lambda: {
                "status": "error",
                "success": False,
                "code": "FILE_NOT_FOUND",
                "pesan": "File tidak ditemukan: sample.txt",
            },
        }
    }
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=EvidenceStore(),
    )

    result = router.execute(ToolRequest(tool="workspace_failure", action="execute", arguments={}))

    assert result.success is False
    assert result.status == "error"
    assert result.error == "FILE_NOT_FOUND: File tidak ditemukan: sample.txt"
    assert result.evidence_id is not None