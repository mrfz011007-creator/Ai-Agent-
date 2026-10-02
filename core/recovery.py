from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.contracts import TaskStatus
from core.task_manager import TaskManager
from verification.evidence import EvidenceStore


class ReconcileOutcome(str, Enum):
    SAFE_TO_RESUME = "SAFE_TO_RESUME"
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    UNKNOWN = "UNKNOWN"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


@dataclass(frozen=True)
class RecoveryDecision:
    task_id: str
    previous_status: TaskStatus
    status: TaskStatus
    action: str
    reason: str


class RecoveryManager:
    """Reconciles interrupted state without assuming an old process is gone safely."""

    INTERRUPTED = (TaskStatus.RUNNING.value, TaskStatus.VERIFYING.value)

    def __init__(self, task_manager: TaskManager, evidence_store: EvidenceStore | None = None):
        self.task_manager = task_manager
        self.evidence_store = evidence_store

    def recover_task(self, task_id: str) -> RecoveryDecision | None:
        task = self.task_manager.get(task_id)
        if task is None:
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

    def reconcile(
        self,
        task_id: str,
        outcome: ReconcileOutcome,
        reason: str,
        evidence_ids: tuple[str, ...] = (),
    ) -> RecoveryDecision:
        task = self.task_manager.get(task_id)
        if task is None:
            task = self.task_manager.restore(task_id)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")

        if task.status != TaskStatus.WAITING:
            raise ValueError(
                f"Reconciliation requires WAITING task, got {task.status}"
            )

        if not reason.strip():
            raise ValueError("Reconciliation requires evidence-backed reason")

        if outcome in (ReconcileOutcome.SAFE_TO_RESUME, ReconcileOutcome.SAFE_TO_RETRY):
            if not evidence_ids or self.evidence_store is None:
                raise ValueError("Safe reconciliation requires persistent evidence IDs")
            evidence = [self.evidence_store.get(eid) for eid in evidence_ids]
            if any(item is None or not item.success for item in evidence):
                raise ValueError("Safe reconciliation requires successful evidence")
            task = self.task_manager.resume(task_id)
            action = "RESUME" if outcome == ReconcileOutcome.SAFE_TO_RESUME else "RETRY"
            return RecoveryDecision(
                task_id=task_id,
                previous_status=TaskStatus.WAITING,
                status=task.status,
                action=action,
                reason=reason,
            )

        task = self.task_manager.block(task_id, reason)
        action = "BLOCK" if outcome == ReconcileOutcome.UNKNOWN else "HUMAN_REQUIRED"
        return RecoveryDecision(
            task_id=task_id,
            previous_status=TaskStatus.WAITING,
            status=task.status,
            action=action,
            reason=reason,
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
