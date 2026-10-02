from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from core.contracts import Task


class PlanStatus(str, Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Plan:
    plan_id: str
    goal: str
    task_ids: tuple[str, ...]
    status: PlanStatus = PlanStatus.PROPOSED
    acceptance_criteria: tuple[str, ...] = ()


class PlanGraphError(ValueError):
    pass


@dataclass
class TaskGraph:
    tasks: dict[str, Task] = field(default_factory=dict)

    def add(self, task: Task) -> Task:
        if task.task_id in self.tasks:
            raise PlanGraphError(f"Duplicate task: {task.task_id}")
        self.tasks[task.task_id] = task
        return task

    def validate(self) -> None:
        for task in self.tasks.values():
            for dep in task.dependencies:
                if dep not in self.tasks:
                    raise PlanGraphError(f"Unknown dependency: {task.task_id} -> {dep}")
                if dep == task.task_id:
                    raise PlanGraphError(f"Self dependency: {task.task_id}")

        state: dict[str, int] = {}

        def visit(task_id: str) -> None:
            color = state.get(task_id, 0)
            if color == 1:
                raise PlanGraphError(f"Dependency cycle detected at: {task_id}")
            if color == 2:
                return
            state[task_id] = 1
            for dep in self.tasks[task_id].dependencies:
                visit(dep)
            state[task_id] = 2

        for task_id in self.tasks:
            visit(task_id)

    def ready(self) -> tuple[Task, ...]:
        self.validate()
        return tuple(
            task for task in self.tasks.values()
            if task.status == task.status.PENDING
            and all(self.tasks[dep].status == task.status.COMPLETED for dep in task.dependencies)
        )

    def blocked_by(self, task_id: str) -> tuple[str, ...]:
        if task_id not in self.tasks:
            raise KeyError(task_id)
        return tuple(
            dep for dep in self.tasks[task_id].dependencies
            if self.tasks[dep].status != self.tasks[dep].status.COMPLETED
        )

    def is_complete(self) -> bool:
        return bool(self.tasks) and all(
            task.status == task.status.COMPLETED for task in self.tasks.values()
        )


@dataclass(frozen=True)
class PlanProposal:
    goal: str
    tasks: tuple[Task, ...]
    acceptance_criteria: tuple[str, ...] = ()

    def graph(self) -> TaskGraph:
        graph = TaskGraph()
        for task in self.tasks:
            graph.add(task)
        graph.validate()
        return graph


class Planner:
    """Deterministic plan validator/builder. Models may propose; Planner validates."""

    def propose(self, goal: str, tasks: Iterable[Task], acceptance_criteria: Iterable[str] = ()) -> PlanProposal:
        goal = goal.strip()
        if not goal:
            raise PlanGraphError("Goal cannot be empty")
        proposal = PlanProposal(goal, tuple(tasks), tuple(acceptance_criteria))
        proposal.graph()
        return proposal

    def materialize(self, proposal: PlanProposal, plan_id: str) -> Plan:
        graph = proposal.graph()
        if not plan_id.strip():
            raise PlanGraphError("Plan ID cannot be empty")
        return Plan(
            plan_id=plan_id,
            goal=proposal.goal,
            task_ids=tuple(graph.tasks),
            status=PlanStatus.VALIDATED,
            acceptance_criteria=proposal.acceptance_criteria,
        )
