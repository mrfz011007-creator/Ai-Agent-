from __future__ import annotations

from dataclasses import dataclass

from core.contracts import Task, TaskStatus, VerificationResult
from core.plan import Plan, PlanProposal, Planner, TaskGraph, PlanStatus
from core.task_manager import TaskManager
from core.state_store import StateStore
from core.model_execution import ModelExecutionService


@dataclass
class Orchestrator:
    """Bounded single-active-task scheduler over a validated plan graph."""

    task_manager: TaskManager
    planner: Planner
    store: StateStore | None = None

    def materialize(self, proposal: PlanProposal, plan_id: str) -> tuple[Plan, TaskGraph]:
        plan = self.planner.materialize(proposal, plan_id)
        graph = proposal.graph()
        for task in graph.tasks.values():
            if self.task_manager.get(task.task_id) is not None:
                raise ValueError(f"Task already exists: {task.task_id}")
        for task in graph.tasks.values():
            self.task_manager.create(task)
        self._persist_plan(plan)
        return plan, graph

    def _persist_plan(self, plan: Plan) -> None:
        if self.store is None:
            return
        self.store.save_plan(
            plan.plan_id,
            plan.goal,
            plan.status.value,
            plan.task_ids,
            plan.acceptance_criteria,
        )

    def persist_plan(self, plan: Plan) -> None:
        self._persist_plan(plan)

    def restore_plan(self, plan_id: str) -> Plan | None:
        if self.store is None:
            return None
        saved = self.store.load_plan(plan_id)
        if saved is None:
            return None
        return Plan(
            plan_id=saved["plan_id"],
            goal=saved["goal"],
            task_ids=tuple(saved["task_ids"]),
            status=PlanStatus(saved["status"]),
            acceptance_criteria=tuple(saved["acceptance_criteria"]),
        )

    def restore_graph(self, plan_id: str) -> tuple[Plan, TaskGraph] | None:
        plan = self.restore_plan(plan_id)
        if plan is None:
            return None
        graph = TaskGraph()
        for task_id in plan.task_ids:
            task = self.task_manager.restore(task_id)
            if task is None:
                raise RuntimeError(f"Persisted task missing: {task_id}")
            graph.add(task)
        graph.validate()
        return plan, graph

    def next_ready(self, graph: TaskGraph) -> Task | None:
        graph.validate()
        active = [task for task in graph.tasks.values()
                  if task.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.WAITING)]
        if active:
            raise RuntimeError(f"Active task already exists: {active[0].task_id}")
        ready = graph.ready()
        return ready[0] if ready else None

    def start_next(self, graph: TaskGraph) -> Task | None:
        task = self.next_ready(graph)
        if task is None:
            return None
        if task.status == TaskStatus.PENDING:
            self.task_manager.mark_ready(task.task_id)
        return self.task_manager.start(task.task_id)

    def complete_task(
        self,
        graph: TaskGraph,
        task_id: str,
        verification: VerificationResult,
    ) -> Task:
        if task_id not in graph.tasks:
            raise KeyError(task_id)
        task = self.task_manager.get(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.status != TaskStatus.VERIFYING:
            raise ValueError("Task must be VERIFYING before completion")
        completed = self.task_manager.complete_with_gate(task_id, verification)
        graph.tasks[task_id] = completed
        return completed

    def plan_complete(self, graph: TaskGraph) -> bool:
        return graph.is_complete()

    def pending_dependencies(self, graph: TaskGraph, task_id: str) -> tuple[str, ...]:
        return graph.blocked_by(task_id)



    def execute_model_step(
        self,
        graph: TaskGraph,
        *,
        model_call,
        execute_proposal,
        verify_execution,
        handle_model_failure=None,
    ) -> Task | None:
        """Execute one task from model intent through runtime authorization and a completion gate."""
        task = self.start_next(graph)
        if task is None:
            return None
        attempt_id = f"{task.task_id}:attempt:{task.attempts}"
        try:
            proposal = ModelExecutionService(model_call).propose(task.title)
            result = execute_proposal(
                proposal,
                task_id=task.task_id,
                attempt_id=attempt_id,
            )
        except Exception as error:
            if handle_model_failure is not None:
                decision = handle_model_failure(task.task_id, error)
                current = self.task_manager.get(task.task_id)
                if current is not None and decision is not None and decision.action != "FAIL":
                    graph.tasks[task.task_id] = current
                    return current
            failed = self.task_manager.fail(task.task_id, str(error))
            graph.tasks[task.task_id] = failed
            return failed

        current = self.task_manager.get(task.task_id)
        if current is None:
            raise KeyError(task.task_id)
        if not result.success:
            if current.status in (TaskStatus.WAITING, TaskStatus.BLOCKED, TaskStatus.FAILED):
                graph.tasks[task.task_id] = current
                return current
            failed = self.task_manager.fail(
                task.task_id, result.error or result.status
            )
            graph.tasks[task.task_id] = failed
            return failed

        evidence_id = result.evidence_id
        if not evidence_id:
            failed = self.task_manager.fail(
                task.task_id, "Successful execution produced no evidence"
            )
            graph.tasks[task.task_id] = failed
            return failed

        current_attempt_id = f"{task.task_id}:attempt:{current.attempts}"
        self.task_manager.begin_verification(task.task_id)
        try:
            verification = verify_execution(
                task_id=task.task_id,
                evidence_ids=(evidence_id,),
                expected_attempt_id=current_attempt_id,
            )
        except Exception as error:
            failed = self.task_manager.fail(task.task_id, str(error))
            graph.tasks[task.task_id] = failed
            return failed

        if verification.status.value != "PASSED":
            failed = self.task_manager.fail(task.task_id, verification.reason)
            graph.tasks[task.task_id] = failed
            return failed

        completed = self.task_manager.complete_with_gate(task.task_id, verification)
        graph.tasks[task.task_id] = completed
        return completed

    def run_model_plan(
        self,
        graph: TaskGraph,
        *,
        model_call,
        execute_proposal,
        verify_execution,
        max_steps: int | None = None,
        handle_model_failure=None,
    ) -> tuple[Task, ...]:
        """Run a bounded model-driven task graph until blocked or complete."""
        completed = []
        steps = 0
        while not self.plan_complete(graph):
            if max_steps is not None and steps >= max_steps:
                break
            task = self.execute_model_step(
                graph,
                model_call=model_call,
                execute_proposal=execute_proposal,
                verify_execution=verify_execution,
                handle_model_failure=handle_model_failure,
            )
            if task is None or task.status != TaskStatus.COMPLETED:
                break
            completed.append(task)
            steps += 1
        return tuple(completed)

    def run_goal(
        self,
        goal: str,
        *,
        plan_id: str,
        plan_proposer,
        model_call,
        execute_proposal,
        verify_execution,
        max_steps: int | None = None,
        handle_model_failure=None,
    ) -> tuple[Plan, TaskGraph, tuple[Task, ...]]:
        """Plan and execute one bounded goal without granting model output authority."""
        proposal = plan_proposer.propose(goal)
        plan, graph = self.materialize(proposal, plan_id)
        completed = self.run_model_plan(
            graph,
            model_call=model_call,
            execute_proposal=execute_proposal,
            verify_execution=verify_execution,
            max_steps=max_steps,
            handle_model_failure=handle_model_failure,
        )
        if self.plan_complete(graph):
            plan = Plan(
                plan_id=plan.plan_id,
                goal=plan.goal,
                task_ids=plan.task_ids,
                status=PlanStatus.COMPLETED,
                acceptance_criteria=plan.acceptance_criteria,
            )
        elif any(task.status == TaskStatus.BLOCKED for task in graph.tasks.values()):
            plan = Plan(
                plan_id=plan.plan_id,
                goal=plan.goal,
                task_ids=plan.task_ids,
                status=PlanStatus.BLOCKED,
                acceptance_criteria=plan.acceptance_criteria,
            )
        elif graph.failed():
            plan = Plan(
                plan_id=plan.plan_id,
                goal=plan.goal,
                task_ids=plan.task_ids,
                status=PlanStatus.FAILED,
                acceptance_criteria=plan.acceptance_criteria,
            )
        elif graph.active():
            plan = Plan(
                plan_id=plan.plan_id,
                goal=plan.goal,
                task_ids=plan.task_ids,
                status=PlanStatus.EXECUTING,
                acceptance_criteria=plan.acceptance_criteria,
            )
        self._persist_plan(plan)
        return plan, graph, completed

    def execute_step(self, graph: TaskGraph, *, execute, verify) -> Task | None:
        """Run exactly one ready task through execution and verification callbacks."""
        task = self.start_next(graph)
        if task is None:
            return None
        try:
            verification = execute(task)
        except Exception as error:
            failed = self.task_manager.fail(task.task_id, str(error))
            graph.tasks[task.task_id] = failed
            return failed
        if verification.status.value != "PASSED":
            failed = self.task_manager.fail(task.task_id, verification.reason)
            graph.tasks[task.task_id] = failed
            return failed
        self.task_manager.begin_verification(task.task_id)
        try:
            final = verify(task, verification)
        except Exception as error:
            failed = self.task_manager.fail(task.task_id, str(error))
            graph.tasks[task.task_id] = failed
            return failed
        if final.status.value != "PASSED":
            failed = self.task_manager.fail(task.task_id, final.reason)
            graph.tasks[task.task_id] = failed
            return failed
        completed = self.task_manager.complete_with_gate(task.task_id, final)
        graph.tasks[task.task_id] = completed
        return completed

    def run_until_blocked(self, graph: TaskGraph, *, execute, verify, max_steps: int | None = None) -> tuple[Task, ...]:
        """Execute a bounded number of graph steps; never runs unboundedly."""
        completed = []
        steps = 0
        while not self.plan_complete(graph):
            if max_steps is not None and steps >= max_steps:
                break
            task = self.execute_step(graph, execute=execute, verify=verify)
            if task is None:
                break
            steps += 1
            if task.status != TaskStatus.COMPLETED:
                break
            completed.append(task)
        return tuple(completed)
