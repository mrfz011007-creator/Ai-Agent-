from __future__ import annotations

from dataclasses import replace

from core.contracts import TaskStatus
from core.model_execution import ExecutionProposal
from core.model_planner import ModelPlanService
from core.plan import Plan, PlanStatus, TaskGraph


class GoalRunner:
    """High-level bounded goal API over planning, execution, recovery, and verification."""

    def __init__(self, runtime):
        self.runtime = runtime

    def _persist(self, plan: Plan, status: PlanStatus) -> Plan:
        updated = replace(plan, status=status)
        self.runtime.orchestrator.persist_plan(updated)
        return updated

    def _status_for_graph(self, graph: TaskGraph) -> PlanStatus:
        if graph.is_complete():
            return PlanStatus.COMPLETED
        if any(task.status == TaskStatus.BLOCKED for task in graph.tasks.values()):
            return PlanStatus.BLOCKED
        if any(task.status in (TaskStatus.WAITING, TaskStatus.RUNNING, TaskStatus.VERIFYING)
               for task in graph.tasks.values()):
            return PlanStatus.WAITING if any(
                task.status == TaskStatus.WAITING for task in graph.tasks.values()
            ) else PlanStatus.EXECUTING
        if any(task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
               for task in graph.tasks.values()):
            return PlanStatus.FAILED
        if not graph.ready():
            return PlanStatus.FAILED
        return PlanStatus.EXECUTING

    def _run_graph(
        self,
        plan: Plan,
        graph: TaskGraph,
        *,
        max_steps: int | None = None,
        project_id: str | None = None,
    ) -> tuple[Plan, TaskGraph]:
        plan = self._persist(plan, PlanStatus.EXECUTING)
        steps = 0
        execution_context = {}

        def execute_with_context(proposal, *, task_id, attempt_id=None):
            if proposal.tool in {"remember", "invalidate_memory"}:
                arguments = dict(proposal.arguments)
                requested_project = arguments.get("project_id")
                if (
                    project_id is not None
                    and requested_project is not None
                    and requested_project != project_id
                ):
                    raise ValueError(
                        "Memory tool project scope does not match active project"
                    )
                if project_id is not None:
                    arguments["project_id"] = project_id

                requested_task = arguments.get("task_id")
                if (
                    requested_task is not None
                    and requested_task != task_id
                ):
                    raise ValueError(
                        "Memory tool task scope does not match active task"
                    )
                if proposal.tool == "remember":
                    arguments["task_id"] = task_id

                proposal = ExecutionProposal(
                    tool=proposal.tool,
                    action=proposal.action,
                    arguments=arguments,
                )

            result = self.runtime.execute_model_proposal(
                proposal,
                task_id=task_id,
                attempt_id=attempt_id,
            )
            execution_context[task_id] = {
                "tool": proposal.tool,
                "action": proposal.action,
                "success": result.success,
                "status": result.status,
                "evidence_id": result.evidence_id,
                "data": result.data,
                "error": result.error,
            }
            return result

        while not graph.is_complete():
            if max_steps is not None and steps >= max_steps:
                plan = self._persist(plan, PlanStatus.WAITING)
                return plan, graph

            from memory_context import build_memory_context

            ready_task = self.runtime.orchestrator.next_ready(graph)
            task_memory_context = ""
            if ready_task is not None:
                task_memory_context = build_memory_context(
                    plan.goal,
                    project_id=project_id,
                    task_id=ready_task.task_id,
                    context=[ready_task.title],
                )
            if task_memory_context:
                execution_context["_memory"] = task_memory_context
            else:
                execution_context.pop("_memory", None)

            task = self.runtime.orchestrator.execute_model_step(
                graph,
                model_call=self.runtime.model_gateway.text,
                execute_proposal=execute_with_context,
                verify_execution=self.runtime.verify_execution_evidence,
                handle_model_failure=self.runtime.handle_model_failure,
                tool_catalog=self.runtime.tool_catalog(),
                model_context=execution_context,
            )
            status = self._status_for_graph(graph)
            plan = self._persist(plan, status)
            if task is None or task.status != TaskStatus.COMPLETED:
                return plan, graph
            steps += 1

        return self._persist(plan, PlanStatus.COMPLETED), graph

    def run(
        self,
        goal: str,
        *,
        plan_id: str | None = None,
        max_steps: int | None = None,
        project_id: str | None = None,
    ) -> tuple[Plan, TaskGraph]:
        """Create and execute one bounded goal through the runtime boundary."""
        from memory_context import build_memory_context

        memory_context = build_memory_context(goal, project_id=project_id)
        proposer = ModelPlanService(self.runtime.model_gateway.generate_text)
        proposal = proposer.propose(goal, memory_context=memory_context)
        resolved_id = plan_id or self._new_plan_id()
        plan, graph = self.runtime.orchestrator.materialize(
            proposal,
            resolved_id,
            project_id=project_id,
        )
        return self._run_graph(
            plan,
            graph,
            max_steps=max_steps,
            project_id=project_id,
        )

    def resume(
        self,
        plan_id: str,
        *,
        max_steps: int | None = None,
        project_id: str | None = None,
    ) -> tuple[Plan, TaskGraph]:
        """Explicitly resume one persisted plan and only its task graph."""
        restored = self.runtime.orchestrator.restore_graph(plan_id)
        if restored is None:
            raise KeyError(f"Unknown persisted plan: {plan_id}")
        plan, graph = restored
        if (
            project_id is not None
            and plan.project_id is not None
            and project_id != plan.project_id
        ):
            raise ValueError(
                "project_id does not match the persisted plan scope"
            )
        project_id = plan.project_id if project_id is None else project_id

        self.runtime.recovery_manager.recover_tasks(tuple(graph.tasks))
        restored = self.runtime.orchestrator.restore_graph(plan_id)
        if restored is None:
            raise KeyError(f"Unknown persisted plan: {plan_id}")
        plan, graph = restored

        waiting = [task for task in graph.tasks.values() if task.status == TaskStatus.WAITING]
        if waiting:
            # WAITING is an explicit recovery boundary. Never turn it into a
            # retry implicitly: interrupted side effects may already have
            # happened, and recovery requires persistent evidence/reconciliation.
            plan = self._persist(plan, PlanStatus.WAITING)
            return plan, graph

        return self._run_graph(
            plan,
            graph,
            max_steps=max_steps,
            project_id=project_id,
        )

    @staticmethod
    def _new_plan_id() -> str:
        import uuid
        return f"plan-{uuid.uuid4().hex[:12]}"
