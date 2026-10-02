from __future__ import annotations

from dataclasses import dataclass, field
from core.contracts import Task, TaskStatus, VerificationResult, VerificationStatus
from core.execution_contract import ExecutionContract
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

    def restore(self, task_id: str) -> Task | None:
        if self.store is None:
            return None
        saved = self.store.load_task(task_id)
        if saved is None:
            return None
        payload = saved["payload"]
        result = payload.get("result")
        if isinstance(result, dict) and "status" in result and "reason" in result:
            result = VerificationResult(
                status=VerificationStatus(result["status"]),
                reason=result["reason"],
                evidence_ids=tuple(result.get("evidence_ids", ())),
                authority=result.get("authority", "verifier"),
            )
        task = Task(
            task_id=saved["task_id"],
            title=payload["title"],
            status=TaskStatus(saved["status"]),
            dependencies=list(payload.get("dependencies", [])),
            execution_contract=(
                ExecutionContract.from_dict(payload["execution_contract"])
                if payload.get("execution_contract") is not None
                else None
            ),
            attempts=int(saved["attempts"]),
            result=result,
        )
        self.tasks[task.task_id] = task
        return task


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

    def retry(self, task_id: str) -> Task:
        """Start a bounded recovery attempt from WAITING state."""
        task = self._require(task_id)
        if task.status != TaskStatus.WAITING:
            raise ValueError(f"Invalid transition: {task.status} -> RUNNING")
        task.attempts += 1
        task.status = TaskStatus.RUNNING
        self._persist(task)
        self._checkpoint(task, event="retry_started", attempt=task.attempts)
        return task

    def resume(self, task_id: str) -> Task:
        task = self._require(task_id)
        if task.status != TaskStatus.WAITING:
            raise ValueError(f"Invalid transition: {task.status} -> RUNNING")
        task.status = TaskStatus.RUNNING
        self._persist(task)
        self._checkpoint(task, event="resumed")
        return task

    def block(self, task_id: str, reason: str) -> Task:
        task = self._require(task_id)
        if task.status != TaskStatus.WAITING:
            raise ValueError(f"Invalid transition: {task.status} -> BLOCKED")
        task.status = TaskStatus.BLOCKED
        task.result = {"reason": reason}
        self._persist(task)
        self._checkpoint(task, event="blocked", reason=reason)
        return task

    def begin_verification(self, task_id: str) -> Task:
        task = self._require(task_id)
        task.mark_verifying()
        self._persist(task)
        self._checkpoint(task, event="verification_started")
        return task

    def complete(self, task_id: str, verification: VerificationResult) -> Task:
        task = self._require(task_id)
        if self.store is not None:
            if not verification.evidence_ids:
                raise ValueError("COMPLETED requires evidence-backed verification")
            for evidence_id in verification.evidence_ids:
                evidence = self.store.load_evidence(evidence_id)
                if evidence is None:
                    raise ValueError(f"Verification evidence not found: {evidence_id}")
                if evidence["task_id"] != task_id:
                    raise ValueError(f"Verification evidence belongs to another task: {evidence_id}")
                if not evidence["success"]:
                    raise ValueError(f"Verification evidence is unsuccessful: {evidence_id}")
        task.complete(verification)
        self._persist(task)
        self._checkpoint(task, event="completed")
        return task

    def complete_with_gate(self, task_id: str, verification: VerificationResult) -> Task:
        """Complete only from a verification result already produced by the acceptance gate."""
        if verification.status != VerificationStatus.PASSED:
            raise ValueError("Task completion requires acceptance-gate verification to PASS")
        if verification.authority != "acceptance_gate":
            raise ValueError("Task completion requires an acceptance-gate verification result")
        return self.complete(task_id, verification)

    def fail(self, task_id: str, reason: str) -> Task:
        task = self._require(task_id)
        if task.status not in (TaskStatus.RUNNING, TaskStatus.VERIFYING):
            raise ValueError(f"Invalid transition: {task.status} -> FAILED")
        task.status = TaskStatus.FAILED
        task.result = {"reason": reason}
        self._persist(task)
        self._checkpoint(task, event="failed", reason=reason)
        return task

    def persist(self, task_id: str) -> None:
        self._persist(self._require(task_id))

    def checkpoint(self, task_id: str, **payload) -> None:
        self._checkpoint(self._require(task_id), **payload)

    def _require(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")
        return task

    
    def _persist(self, task: Task) -> None:
        if self.store is None:
            return
        result = task.result
        if isinstance(result, VerificationResult):
            result = {
                "type": "VerificationResult",
                "status": result.status.value,
                "reason": result.reason,
                "evidence_ids": list(result.evidence_ids),
                "authority": result.authority,
            }
        self.store.save_task(
            task_id=task.task_id,
            status=task.status.value,
            attempts=task.attempts,
            payload={
                "title": task.title,
                "dependencies": task.dependencies,
                "execution_contract": (
                    task.execution_contract.to_dict()
                    if task.execution_contract is not None
                    else None
                ),
                "result": result,
            },
        )

    def _checkpoint(self, task: Task, **payload) -> None:
        if self.checkpoints is not None:
            self.checkpoints.capture(task, **payload)
