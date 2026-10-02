from core.contracts import ToolRequest
from core.budget import Budget
from core.budget import BudgetManager
from core.state_store import StateStore
from execution.router import ToolRouter
from security.policy import PolicyEngine
from verification.evidence import EvidenceStore


def _router(registry, confirmation=None):
    evidence = EvidenceStore()
    return ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget()),
        evidence=evidence,
        confirmation=confirmation,
    )


def test_safe_tool_executes():
    registry = {
        "lokasi": {
            "permission": "safe",
            "parameters": {"type": "object", "properties": {}},
            "func": lambda: {"success": True, "status": "success"},
        }
    }
    result = _router(registry).execute(ToolRequest("lokasi", "execute"))
    assert result.success
    assert result.evidence_id


def test_unknown_tool_is_denied():
    result = _router({}).execute(ToolRequest("tool_tidak_dikenal", "execute"))
    assert not result.success
    assert result.status == "denied"


def test_confirm_tool_uses_confirmation_callback():
    registry = {
        "buat_file": {
            "permission": "confirm",
            "parameters": {
                "type": "object",
                "properties": {"nama": {"type": "string"}},
                "required": ["nama"],
            },
            "func": lambda nama: {"success": True, "nama": nama},
        }
    }
    result = _router(registry, confirmation=lambda *_: False).execute(
        ToolRequest("buat_file", "execute", {"nama": "test_permission.txt"})
    )
    assert not result.success
    assert result.status == "cancelled"
