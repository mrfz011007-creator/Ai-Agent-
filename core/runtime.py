from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.budget import BudgetManager
from core.contracts import Budget, ToolRequest
from core.state_store import StateStore
from core.checkpoint import CheckpointManager
from core.task_manager import TaskManager
from core.recovery import RecoveryManager
from security.policy import PolicyEngine
from execution.router import ToolRouter
from verification.evidence import EvidenceStore
from verification.artifacts import ArtifactManager
from verification.build import BuildManager, TestManager
from registry import TOOL_REGISTRY
from permissions import minta_konfirmasi


@dataclass
class AgentRuntime:
    state_store: StateStore
    checkpoint_manager: CheckpointManager
    task_manager: TaskManager
    recovery_manager: RecoveryManager
    evidence_store: EvidenceStore
    budget_manager: BudgetManager
    policy_engine: PolicyEngine
    tool_router: ToolRouter
    artifact_manager: ArtifactManager
    build_manager: BuildManager
    test_manager: TestManager

    @classmethod
    def create(cls, state_path: str | Path | None = None) -> "AgentRuntime":
        path = state_path or os.environ.get("AI_AGENT_STATE_DB", "agent_state.sqlite3")
        state_store = StateStore(path)
        checkpoint_manager = CheckpointManager(state_store)
        task_manager = TaskManager(
            store=state_store,
            checkpoints=checkpoint_manager,
        )
        evidence_store = EvidenceStore(state_store)
        recovery_manager = RecoveryManager(task_manager, evidence_store)
        artifact_manager = ArtifactManager(state_store)
        build_manager = BuildManager(artifact_manager)
        test_manager = TestManager()
        budget_manager = BudgetManager(Budget())
        policy_engine = PolicyEngine(TOOL_REGISTRY.get)
        tool_router = ToolRouter(
            registry_getter=TOOL_REGISTRY.get,
            policy=policy_engine,
            budget=budget_manager,
            evidence=evidence_store,
            confirmation=minta_konfirmasi,
        )
        return cls(
            state_store=state_store,
            checkpoint_manager=checkpoint_manager,
            task_manager=task_manager,
            recovery_manager=recovery_manager,
            evidence_store=evidence_store,
            budget_manager=budget_manager,
            policy_engine=policy_engine,
            tool_router=tool_router,
            artifact_manager=artifact_manager,
            build_manager=build_manager,
            test_manager=test_manager,
        )

    def build(self, **kwargs):
        return self.build_manager.build(**kwargs)

    def run_tests(self, **kwargs):
        return self.test_manager.run(**kwargs)

    def recover_task(self, task_id: str):
        return self.recovery_manager.recover_task(task_id)

    def recover_interrupted(self):
        return self.recovery_manager.recover_interrupted()


_default_runtime: AgentRuntime | None = None


def get_runtime() -> AgentRuntime:
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = AgentRuntime.create()
    return _default_runtime


def get_tool_router() -> ToolRouter:
    return get_runtime().tool_router


def execute_tool(
    name: str,
    args: dict,
    *,
    source: str = "agent",
    task_id: str | None = None,
):
    return get_tool_router().execute(
        ToolRequest(
            tool=name,
            action="execute",
            arguments=args,
            source=source,
            task_id=task_id,
        )
    )
