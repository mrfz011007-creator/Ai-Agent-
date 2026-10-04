from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from core.contracts import Task, TaskStatus
from core.execution_contract import ExecutionContract, ExecutionContractError
from verification.criteria import validate_criteria, CriterionValidationError


class PlanStatus(str, Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    EXECUTING = "EXECUTING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Plan:
    plan_id: str
    goal: str
    task_ids: tuple[str, ...]
    status: PlanStatus = PlanStatus.PROPOSED
    acceptance_criteria: tuple[Mapping[str, object], ...] = ()
    project_id: str | None = None


class PlanGraphError(ValueError):
    pass


MAX_PLAN_TASKS = 50
MAX_GOAL_CHARS = 12000
MAX_TASK_ID_CHARS = 200
MAX_TASK_TITLE_CHARS = 2000
MAX_TASK_DEPENDENCIES = 50
MAX_PLAN_CRITERIA = 50


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
            if task.status == TaskStatus.PENDING
            and all(self.tasks[dep].status == TaskStatus.COMPLETED for dep in task.dependencies)
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
            task.status == TaskStatus.COMPLETED for task in self.tasks.values()
        )

    def active(self) -> tuple[Task, ...]:
        return tuple(
            task for task in self.tasks.values()
            if task.status in (
                TaskStatus.READY,
                TaskStatus.RUNNING,
                TaskStatus.VERIFYING,
                TaskStatus.WAITING,
            )
        )

    def failed(self) -> tuple[Task, ...]:
        return tuple(
            task for task in self.tasks.values()
            if task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED)
        )


@dataclass(frozen=True)
class PlanProposal:
    goal: str
    tasks: tuple[Task, ...]
    acceptance_criteria: tuple[Mapping[str, object], ...] = ()

    def graph(self) -> TaskGraph:
        graph = TaskGraph()
        for task in self.tasks:
            graph.add(task)
        graph.validate()
        return graph


class Planner:
    """Deterministic plan validator/builder. Models may propose; Planner validates."""

    def propose(self, goal: str, tasks: Iterable[Task], acceptance_criteria: Iterable[Mapping[str, object]] = ()) -> PlanProposal:
        goal = goal.strip()
        if not goal:
            raise PlanGraphError("Goal cannot be empty")
        try:
            criteria = validate_criteria(tuple(acceptance_criteria))
        except CriterionValidationError as error:
            raise PlanGraphError(f"Invalid machine-verifiable acceptance criteria: {error}") from error
        proposal = PlanProposal(goal, tuple(tasks), criteria)
        proposal.graph()
        return proposal

    def materialize(
        self,
        proposal: PlanProposal,
        plan_id: str,
        *,
        project_id: str | None = None,
    ) -> Plan:
        graph = proposal.graph()
        if not plan_id.strip():
            raise PlanGraphError("Plan ID cannot be empty")
        return Plan(
            plan_id=plan_id,
            goal=proposal.goal,
            task_ids=tuple(graph.tasks),
            status=PlanStatus.VALIDATED,
            acceptance_criteria=proposal.acceptance_criteria,
            project_id=project_id,
        )


class PlanDecoder:
    """Convert untrusted model output into a validated PlanProposal."""

    _SAFE_READ_ONLY_TOOLS = (
        "lihat",
        "lokasi",
        "siapa",
        "cari_teks",
        "baca_file",
        "recall",
        "search_memory",
        "remember",
    )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> PlanProposal:
        if not isinstance(payload, Mapping):
            raise PlanGraphError("Model plan must be an object")
        goal = payload.get("goal")
        raw_tasks = payload.get("tasks")
        criteria = payload.get("acceptance_criteria", ())
        if not isinstance(goal, str) or not goal.strip():
            raise PlanGraphError("Model plan goal must be a non-empty string")
        if len(goal) > MAX_GOAL_CHARS:
            raise PlanGraphError(f"Model plan goal exceeds {MAX_GOAL_CHARS} characters")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise PlanGraphError("Model plan tasks must be a non-empty list")
        if len(raw_tasks) > MAX_PLAN_TASKS:
            raise PlanGraphError(f"Model plan exceeds task limit: {MAX_PLAN_TASKS}")
        if isinstance(criteria, (list, tuple)) and len(criteria) > MAX_PLAN_CRITERIA:
            raise PlanGraphError(
                f"Model plan exceeds acceptance-criteria limit: {MAX_PLAN_CRITERIA}"
            )
        try:
            criteria = validate_criteria(criteria)
        except CriterionValidationError as error:
            raise PlanGraphError(
                f"Invalid machine-verifiable acceptance criteria: {error}"
            ) from error
        tasks = []
        for item in raw_tasks:
            if not isinstance(item, Mapping):
                raise PlanGraphError("Each model task must be an object")
            task_id, title = item.get("task_id"), item.get("title")
            dependencies = item.get("dependencies", ())
            if not isinstance(task_id, str) or not task_id.strip():
                raise PlanGraphError("Task ID must be a non-empty string")
            if not isinstance(title, str) or not title.strip():
                raise PlanGraphError(f"Task title missing: {task_id}")
            if not isinstance(dependencies, (list, tuple)) or not all(isinstance(dep, str) and dep.strip() for dep in dependencies):
                raise PlanGraphError(f"Invalid dependencies: {task_id}")
            if len(dependencies) > MAX_TASK_DEPENDENCIES:
                raise PlanGraphError(f"Too many dependencies: {task_id}")
            contract_payload = item.get("execution_contract")
            if contract_payload is None:
                execution_contract = ExecutionContract(
                    objective=title.strip(),
                    allowed_tools=cls._SAFE_READ_ONLY_TOOLS,
                    allowed_capabilities=("workspace.read", "workspace.write"),
                    completion_conditions=(
                        {"type": "evidence_success", "task_id": task_id.strip()},
                    ),
                )
            else:
                try:
                    execution_contract = ExecutionContract.from_dict(contract_payload)
                except (ExecutionContractError, TypeError, ValueError) as error:
                    raise PlanGraphError(
                        f"Invalid execution contract: {task_id}: {error}"
                    ) from error

            tasks.append(
                Task(
                    task_id=task_id.strip(),
                    title=title.strip(),
                    dependencies=list(dependencies),
                    execution_contract=execution_contract,
                )
            )
        return PlanProposal(goal=goal.strip(), tasks=tuple(tasks), acceptance_criteria=tuple(dict(item) for item in criteria))
