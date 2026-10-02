from __future__ import annotations

from dataclasses import dataclass

from core.contracts import TaskStatus
from core.task_manager import TaskManager


@dataclass(frozen=True)
class RecoveryDecision:
    task_id: str
    previous_status: TaskStatus
    status: TaskStatus
    action: str
    reason: str


class RecoveryManager:
    """Reconciles interrupted task state after a process restart.

    Interrupted execution is never resumed blindly. RUNNING and VERIFYING
    tasks are moved to WAITING until a later reconciliation/resume decision.
    """

    INTERRUPTED = (TaskStatus.RUNNING.value, TaskStatus.VERIFYING.value)

    def __init__(self, task_manager: TaskManager):
        self.task_manager = task_manager

    def recover_task(self, task_id: str) -> RecoveryDecision | None:
        task = self.task_manager.restore(task_id)
        if task is None:
            return None

        previous = task.status
        if previous not in (TaskStatus.RUNNING, TaskStatus.VERIFYING):
            return RecoveryDecision(
                task_id=task.task_id,
                previous_status=previous,
                status=previous,
                action="NO_ACTION",
                reason="Task is not interrupted.",
            )

        task.status = TaskStatus.WAITING
        self.task_manager.persist(task.task_id)
        self.task_manager.checkpoint(
            task.task_id,
            event="recovery_required",
            previous_status=previous.value,
        )
        return RecoveryDecision(
            task_id=task.task_id,
            previous_status=previous,
            status=TaskStatus.WAITING,
            action="WAIT_FOR_RECONCILIATION",
            reason="Execution state was interrupted and requires reconciliation before retry.",
        )

    def recover_interrupted(self) -> list[RecoveryDecision]:
        if self.task_manager.store is None:
            return []

        rows = self.task_manager.store.load_tasks_by_status(list(self.INTERRUPTED))
        decisions = []
        for row in rows:
            decision = self.recover_task(row["task_id"])
            if decision is not None:
                decisions.append(decision)
        return decisions
