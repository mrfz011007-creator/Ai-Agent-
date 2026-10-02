from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import Task, TaskStatus, VerificationResult
from core.state_store import StateStore
from core.checkpoint import CheckpointManager


@dataclass
class TaskManager:
    tasks: dict[str, Task] = field(default_factory=dict)
    store: StateStore | None = None
    checkpoints: CheckpointManager | None = None

    def create(self, task: Task) -> Task:
        if task.task_id in self.tasks:
            raise ValueError(f"Task already exists: {task.task_id}")
        self.tasks[task.task_id] = task
        self._persist(task)
        return task

    def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def mark_ready(self, task_id: str) -> Task:
        task = self._require(task_id)
        if task.status != TaskStatus.PENDING:
            raise ValueError(f"Invalid transition: {task.status} -> READY")
        if any(
            self._require(dep).status != TaskStatus.COMPLETED
            for dep in task.dependencies
        ):
            raise ValueError("Task dependencies are not completed")
        task.status = TaskStatus.READY
        self._persist(task)
        return task

    def start(self, task_id: str) -> Task:
        task = self._require(task_id)
        task.attempts += 1
        task.mark_running()
        self._persist(task)
        self._checkpoint(task, event="started")
        return task

    def begin_verification(self, task_id: str) -> Task:
        task = self._require(task_id)
        task.mark_verifying()
        self._persist(task)
        self._checkpoint(task, event="verification_started")
        return task

    def complete(self, task_id: str, verification: VerificationResult) -> Task:
        task = self._require(task_id)
        task.complete(verification)
        self._persist(task)
        self._checkpoint(task, event="completed")
        return task

    def fail(self, task_id: str, reason: str) -> Task:
        task = self._require(task_id)
        if task.status not in (TaskStatus.RUNNING, TaskStatus.VERIFYING):
            raise ValueError(f"Invalid transition: {task.status} -> FAILED")
        task.status = TaskStatus.FAILED
        task.result = {"reason": reason}
        self._persist(task)
        self._checkpoint(task, event="failed", reason=reason)
        return task

    def _require(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")
        return task
