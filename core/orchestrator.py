from __future__ import annotations

from dataclasses import dataclass

from core.contracts import Task, TaskStatus, VerificationResult
from core.plan import Plan, PlanProposal, Planner, TaskGraph
from core.task_manager import TaskManager


@dataclass
class Orchestrator:
    """Bounded single-active-task scheduler over a validated plan graph."""

    task_manager: TaskManager
    planner: Planner

    def materialize(self, proposal: PlanProposal, plan_id: str) -> tuple[Plan, TaskGraph]:
        plan = self.planner.materialize(proposal, plan_id)
        graph = proposal.graph()
        for task in graph.tasks.values():
            if self.task_manager.get(task.task_id) is not None:
                raise ValueError(f"Task already exists: {task.task_id}")
        for task in graph.tasks.values():
            self.task_manager.create(task)
        return plan, graph

    def next_ready(self, graph: TaskGraph) -> Task | None:
        graph.validate()
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
        completed = self.task_manager.complete(task_id, verification)
        graph.tasks[task_id] = completed
        return completed

    def plan_complete(self, graph: TaskGraph) -> bool:
        return graph.is_complete()

    def pending_dependencies(self, graph: TaskGraph, task_id: str) -> tuple[str, ...]:
        return graph.blocked_by(task_id)
