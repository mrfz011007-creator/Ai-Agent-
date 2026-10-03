from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.budget import BudgetManager
from core.model_gateway import ModelGateway, create_gemini_gateway
from core.model_planner import ModelPlanService
from core.plan import Plan, TaskGraph
from core.model_execution import ExecutionProposal
from core.contracts import Budget, ToolRequest, TaskStatus
from core.state_store import StateStore
from core.checkpoint import CheckpointManager
from core.task_manager import TaskManager
from core.orchestrator import Orchestrator
from core.plan import Planner
from core.recovery import RecoveryManager, RecoveryController
from security.policy import PolicyEngine
from execution.router import ToolRouter
from verification.evidence import EvidenceStore
from verification.artifacts import ArtifactManager
from verification.build import BuildManager, TestManager
from verification.verifier import Verifier
from verification.acceptance import AcceptanceGate
from registry import TOOL_REGISTRY
from permissions import minta_konfirmasi
from execution.command import run_command
from security.guard import GuardEngine
from security.capabilities import CapabilityPolicy


@dataclass
class AgentRuntime:
    state_store: StateStore
    checkpoint_manager: CheckpointManager
    task_manager: TaskManager
    recovery_manager: RecoveryManager
    recovery_controller: RecoveryController
    evidence_store: EvidenceStore
    budget_manager: BudgetManager
    policy_engine: PolicyEngine
    tool_router: ToolRouter
    artifact_manager: ArtifactManager
    build_manager: BuildManager
    test_manager: TestManager
    acceptance_gate: AcceptanceGate
    model_gateway: ModelGateway
    orchestrator: Orchestrator

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
        artifact_manager = ArtifactManager(state_store)
        budget_manager = BudgetManager(Budget())
        recovery_manager = RecoveryManager(task_manager, evidence_store, budget_manager)
        recovery_controller = RecoveryController(recovery_manager, task_manager)
        model_gateway = create_gemini_gateway(
            budget=budget_manager,
        )

        runtime_registry = dict(TOOL_REGISTRY)

        registry_getter = runtime_registry.get
        policy_engine = PolicyEngine(registry_getter)
        capability_policy = CapabilityPolicy(registry_getter)
        workspace_root = Path(
            os.environ.get("AI_AGENT_WORKSPACE_ROOT", os.getcwd())
        ).resolve()
        guard = GuardEngine(workspace_root)
        tool_router = ToolRouter(
            registry_getter=registry_getter,
            policy=policy_engine,
            budget=budget_manager,
            evidence=evidence_store,
            confirmation=minta_konfirmasi,
            guard=guard,
            task_getter=task_manager.get,
            capability_policy=capability_policy,
            task_tool_call_consumer=task_manager.consume_tool_call,
            task_tool_call_refunder=task_manager.refund_tool_call,
        )
        build_manager = BuildManager(artifact_manager, tool_router)
        test_manager = TestManager(tool_router)
        acceptance_gate = AcceptanceGate(Verifier(evidence_store), artifact_manager)
        orchestrator = Orchestrator(task_manager, Planner(), store=state_store)
        return cls(
            state_store=state_store,
            checkpoint_manager=checkpoint_manager,
            task_manager=task_manager,
            recovery_manager=recovery_manager,
            recovery_controller=recovery_controller,
            evidence_store=evidence_store,
            budget_manager=budget_manager,
            policy_engine=policy_engine,
            tool_router=tool_router,
            artifact_manager=artifact_manager,
            build_manager=build_manager,
            test_manager=test_manager,
            acceptance_gate=acceptance_gate,
            model_gateway=model_gateway,
            orchestrator=orchestrator,
        )

    def propose_memory_candidate(self, **kwargs):
        from core.memory_learning import propose_memory_candidate
        return propose_memory_candidate(self.state_store, **kwargs)

    def list_memory_candidates(self, **kwargs):
        from core.memory_learning import list_memory_candidates
        return list_memory_candidates(self.state_store, **kwargs)

    def commit_memory_candidate(self, candidate_id: str, *, reason: str):
        from core.memory_learning import commit_memory_candidate
        return commit_memory_candidate(
            self.state_store,
            candidate_id,
            reason=reason,
        )

    def reject_memory_candidate(self, candidate_id: str, *, reason: str):
        from core.memory_learning import reject_memory_candidate
        return reject_memory_candidate(
            self.state_store,
            candidate_id,
            reason=reason,
        )

    def tool_catalog(self) -> dict[str, dict]:
        """Return non-executable tool metadata for model context."""
        from registry import get_tool_catalog
        return get_tool_catalog()

    def run_goal(
        self,
        goal: str,
        *,
        plan_id: str | None = None,
        max_steps: int | None = None,
        project_id: str | None = None,
    ):
        """Execute a complete bounded goal through the persistent GoalRunner."""

        from core.goal_runner import GoalRunner
        return GoalRunner(self).run(
            goal,
            plan_id=plan_id,
            max_steps=max_steps,
            project_id=project_id,
        )

    def resume_goal(
        self,
        plan_id: str,
        *,
        max_steps: int | None = None,
        project_id: str | None = None,
    ):
        """Resume one persisted goal without touching unrelated plans."""
        from core.goal_runner import GoalRunner
        return GoalRunner(self).resume(plan_id, max_steps=max_steps, project_id=project_id)

    def plan_goal(
        self,
        goal: str,
        *,
        plan_id: str | None = None,
        project_id: str | None = None,
    ):
        """Create a validated model-proposed plan and persist it without executing it."""
        import uuid
        if not goal.strip():
            raise ValueError("Goal cannot be empty")
        from memory_context import build_memory_context
        proposer = ModelPlanService(self.model_gateway.generate_text)
        proposal = proposer.propose(
            goal,
            memory_context=build_memory_context(goal, project_id=project_id),
        )
        resolved_plan_id = plan_id or f"plan-{uuid.uuid4().hex[:12]}"
        return self.orchestrator.materialize(
            proposal,
            resolved_plan_id,
            project_id=project_id,
        )

    def resume_plan(self, plan_id: str):
        """Restore a persisted plan and reconcile interrupted tasks before execution."""
        restored = self.orchestrator.restore_graph(plan_id)
        if restored is None:
            raise KeyError(f"Unknown persisted plan: {plan_id}")
        plan, graph = restored
        self.recovery_manager.recover_tasks(tuple(graph.tasks))
        restored = self.orchestrator.restore_graph(plan_id)
        if restored is None:
            raise KeyError(f"Unknown persisted plan: {plan_id}")
        plan, graph = restored
        return plan, graph

    def execute_model_proposal(
        self,
        proposal: ExecutionProposal,
        *,
        task_id: str,
        attempt_id: str | None = None,
    ):
        """Send model intent through the bounded recovery-aware execution path."""
        return self.execute_with_recovery(
            proposal.tool,
            dict(proposal.arguments),
            source="model",
            task_id=task_id,
            attempt_id=attempt_id,
        )

    def handle_model_failure(self, task_id: str | None, error: Exception):
        return self.recovery_controller.handle_model_failure(task_id, error)

    def execute_with_recovery(self, name: str, args: dict, *, source: str = "agent", task_id: str | None = None, attempt_id: str | None = None):
        """Execute a tool and perform at most one bounded recovery retry."""
        task = self.task_manager.get(task_id) if task_id is not None else None
        current_attempt = attempt_id or (f"{task_id}:attempt:{task.attempts}" if task_id and task else None)
        result = self.tool_router.execute(ToolRequest(tool=name, action="execute", arguments=args, source=source, task_id=task_id, attempt_id=current_attempt))
        if result.success or task_id is None:
            return result

        # Retrying an operation after an ambiguous failure can duplicate side effects.
        # Only tools explicitly declared idempotent may be replayed automatically.
        metadata = self.tool_router._registry_getter(name)
        if metadata is None or metadata.get("idempotent") is not True:
            return result

        decision = self.recovery_controller.handle_tool_failure(
            task_id,
            status=result.status,
            error=result.error,
            idempotent=True,
        )
        if decision.action != "RETRY":
            return result

        task = self.task_manager.get(task_id) if task_id is not None else None
        current_attempt = (
            f"{task_id}:attempt:{task.attempts}"
            if task_id and task
            else attempt_id
        )
        return self.tool_router.execute(
            ToolRequest(
                tool=name,
                action="execute",
                arguments=args,
                source=source,
                task_id=task_id,
                attempt_id=current_attempt,
            )
        )

    def build(self, **kwargs):
        return self.build_manager.build(**kwargs)

    def run_tests(self, **kwargs):
        return self.test_manager.run(**kwargs)

    def verify_acceptance(self, **kwargs):
        return self.acceptance_gate.verify(**kwargs)

    def verify_execution_evidence(
        self,
        task_id: str,
        evidence_ids: tuple[str, ...] | list[str],
        expected_attempt_id: str | None = None,
    ):
        """Verify execution and move the task to VERIFYING without completing it."""
        verification = self.acceptance_gate.verify_execution(
            task_id=task_id,
            evidence_ids=tuple(evidence_ids),
            expected_attempt_id=expected_attempt_id,
        )
        if verification.status.value == "PASSED":
            task = self.task_manager.get(task_id) or self.task_manager.restore(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status == TaskStatus.RUNNING:
                self.task_manager.begin_verification(task_id)
            elif task.status != TaskStatus.VERIFYING:
                raise ValueError(f"Task must be RUNNING or VERIFYING, got {task.status}")
        return verification

    def verify_tool_execution(self, task_id: str, evidence_ids: list[str], expected_attempt_id: str | None = None):
        """Verify execution evidence through the acceptance gate."""
        verification = self.acceptance_gate.verify_execution(
            task_id=task_id,
            evidence_ids=tuple(evidence_ids),
            expected_attempt_id=expected_attempt_id,
        )
        if verification.status.value != "PASSED":
            return verification
        task = self.task_manager.get(task_id) or self.task_manager.restore(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.status == TaskStatus.RUNNING:
            self.task_manager.begin_verification(task_id)
        elif task.status != TaskStatus.VERIFYING:
            raise ValueError(f"Task must be RUNNING or VERIFYING, got {task.status}")
        return self.task_manager.complete_with_gate(task_id, verification)

    def verify_and_complete(self, task_id: str, **kwargs):
        verification = self.acceptance_gate.verify(task_id=task_id, **kwargs)
        if verification.status.value != "PASSED":
            return verification
        self.task_manager.begin_verification(task_id)
        return self.task_manager.complete_with_gate(task_id, verification)

    def reconcile_tool_execution(
        self,
        request_id: str,
        *,
        status: str,
        reason: str,
        evidence_id: str | None = None,
    ) -> None:
        """Explicitly resolve an uncertain tool execution before replay."""
        self.state_store.reconcile_tool_execution(
            request_id,
            status=status,
            reason=reason,
            evidence_id=evidence_id,
        )

    def reconcile_task(
        self,
        task_id: str,
        *,
        outcome,
        reason: str,
        evidence_ids: tuple[str, ...] = (),
    ):
        """Explicitly reconcile a WAITING task before resume/retry."""
        from core.recovery import ReconcileOutcome

        if isinstance(outcome, str):
            outcome = ReconcileOutcome(outcome)
        return self.recovery_manager.reconcile(
            task_id,
            outcome,
            reason,
            tuple(evidence_ids),
        )

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
