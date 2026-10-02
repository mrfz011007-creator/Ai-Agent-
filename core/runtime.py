from __future__ import annotations

from core.budget import BudgetManager
from core.contracts import Budget, ToolRequest
from core.state_store import StateStore
from core.checkpoint import CheckpointManager
from core.task_manager import TaskManager
from security.policy import PolicyEngine
from execution.router import ToolRouter
from verification.evidence import EvidenceStore
from registry import TOOL_REGISTRY
from permissions import minta_konfirmasi


state_store = StateStore()
checkpoint_manager = CheckpointManager(state_store)
task_manager = TaskManager(store=state_store, checkpoints=checkpoint_manager)
evidence_store = EvidenceStore()
budget_manager = BudgetManager(Budget())
policy_engine = PolicyEngine(TOOL_REGISTRY.get)
tool_router = ToolRouter(
    registry_getter=TOOL_REGISTRY.get,
    policy=policy_engine,
    budget=budget_manager,
    evidence=evidence_store,
    confirmation=minta_konfirmasi,
)


def get_tool_router() -> ToolRouter:
    return tool_router


def execute_tool(
    name: str,
    args: dict,
    *,
    source: str = "agent",
    task_id: str | None = None,
):
    return tool_router.execute(
        ToolRequest(
            tool=name,
            action="execute",
            arguments=args,
            source=source,
            task_id=task_id,
        )
    )
