from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.contracts import TaskStatus
from core.task_manager import TaskManager
from verification.evidence import EvidenceStore


class FailureClass(str, Enum):
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


def classify_failure(status: str, error: str | None = None) -> FailureClass:
    text = f"{status} {error or ''}".lower()
    if any(token in text for token in ("budget_exceeded", "guard_denied", "user denied", "cancelled", "permission")):
        return FailureClass.HUMAN_REQUIRED if "budget" in text else FailureClass.NON_RETRYABLE
    if any(token in text for token in ("timeout", "timed out", "temporarily", "unavailable", "connection", "429", "rate limit")):
        return FailureClass.RETRYABLE
    return FailureClass.NON_RETRYABLE


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

    def __init__(self, task_manager: TaskManager, evidence_store: EvidenceStore | None = None, budget=None):
        self.task_manager = task_manager
        self.evidence_store = evidence_store
        self.budget = budget

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

    def retry_after_failure(self, task_id: str, *, status: str, error: str | None = None) -> RecoveryDecision:
        task = self.task_manager.get(task_id) or self.task_manager.restore(task_id)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")
        if task.status != TaskStatus.RUNNING:
            raise ValueError(f"Retry requires RUNNING task, got {task.status}")
        failure_class = classify_failure(status, error)
        if failure_class != FailureClass.RETRYABLE:
            return RecoveryDecision(task_id, task.status, task.status, failure_class.value, error or status)
        if self.budget is None:
            raise RuntimeError("Recovery budget is not configured")
        try:
            self.budget.reserve_recovery_cycle()
        except RuntimeError as exc:
            self.task_manager.fail(task_id, str(exc))
            return RecoveryDecision(task_id, TaskStatus.RUNNING, TaskStatus.FAILED, "BLOCK", str(exc))
        task.status = TaskStatus.WAITING
        self.task_manager.persist(task_id)
        self.task_manager.checkpoint(task_id, event="recovery_retry", reason=error or status)
        task = self.task_manager.retry(task_id)
        return RecoveryDecision(task_id, TaskStatus.WAITING, task.status, "RETRY", error or status)

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


class RecoveryController:
    """Single failure entry point for model and tool execution recovery."""

    def __init__(self, recovery_manager: RecoveryManager, task_manager: TaskManager):
        self.recovery_manager = recovery_manager
        self.task_manager = task_manager

    def handle_tool_failure(
        self,
        task_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> RecoveryDecision:
        task = self.task_manager.get(task_id) or self.task_manager.restore(task_id)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")

        failure_class = classify_failure(status, error)
        if failure_class == FailureClass.RETRYABLE:
            return self.recovery_manager.retry_after_failure(
                task_id, status=status, error=error
            )

        reason = error or status
        previous = task.status
        if failure_class == FailureClass.HUMAN_REQUIRED:
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.WAITING
                self.task_manager.persist(task_id)
                self.task_manager.checkpoint(
                    task_id, event="recovery_human_required", reason=reason
                )
            task = self.task_manager.block(task_id, reason)
            return RecoveryDecision(
                task_id, previous, task.status, "HUMAN_REQUIRED", reason
            )

        task = self.task_manager.fail(task_id, reason)
        return RecoveryDecision(
            task_id, previous, task.status, "FAIL", reason
        )

    def handle_model_failure(
        self,
        task_id: str | None,
        error: Exception,
    ) -> RecoveryDecision | None:
        if task_id is None:
            return None

        message = str(error)
        if any(
            marker in message
            for marker in (
                "MODEL_CREDENTIALS_EXHAUSTED",
                "NO_MODEL_CREDENTIAL_AVAILABLE",
            )
        ):
            task = self.task_manager.get(task_id) or self.task_manager.restore(task_id)
            if task is None:
                raise KeyError(f"Unknown task: {task_id}")
            previous = task.status
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.WAITING
                self.task_manager.persist(task_id)
                self.task_manager.checkpoint(
                    task_id, event="model_waiting", reason=message
                )
            return RecoveryDecision(
                task_id, previous, task.status, "WAIT_FOR_MODEL", message
            )

        if "MODEL_BUDGET_EXCEEDED" in message:
            task = self.task_manager.get(task_id) or self.task_manager.restore(task_id)
            if task is None:
                raise KeyError(f"Unknown task: {task_id}")
            previous = task.status
            if task.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING):
                task = self.task_manager.fail(task_id, message)
            return RecoveryDecision(
                task_id, previous, task.status, "FAIL", message
            )

        return None
