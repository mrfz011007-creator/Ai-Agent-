from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
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
        if self.store is not None and self.store.load_task(task.task_id) is not None:
            raise ValueError(f"Persisted task already exists: {task.task_id}")
        self.tasks[task.task_id] = task
        try:
            self._persist(task)
        except Exception:
            self.tasks.pop(task.task_id, None)
            raise
        return task

    def get(self, task_id: str) -> Task | None:
        task = self.tasks.get(task_id)
        if task is not None:
            return task
        return self.restore(task_id)

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
            tool_calls=int(payload.get("tool_calls", 0)),
        )
        self.tasks[task.task_id] = task
        return task


    def consume_tool_call(self, task_id: str) -> Task:
        task = self._require(task_id)
        contract = task.execution_contract
        if contract is not None and task.tool_calls >= contract.max_tool_calls:
            raise RuntimeError("TASK_TOOL_CALL_LIMIT_EXCEEDED")
        return self._mutate_and_persist(
            task, lambda: setattr(task, "tool_calls", task.tool_calls + 1)
        )

    def refund_tool_call(self, task_id: str) -> Task:
        """Compensate a task-local tool reservation when setup cannot continue."""
        task = self._require(task_id)
        if task.tool_calls <= 0:
            raise RuntimeError("TASK_TOOL_CALL_REFUND_UNDERFLOW")
        return self._mutate_and_persist(
            task, lambda: setattr(task, "tool_calls", task.tool_calls - 1)
        )

    def mark_ready(self, task_id: str) -> Task:
        task = self._require(task_id)
        if task.status != TaskStatus.PENDING:
            raise ValueError(f"Invalid transition: {task.status} -> READY")
        if any(
            self._require(dep).status != TaskStatus.COMPLETED
            for dep in task.dependencies
        ):
            raise ValueError("Task dependencies are not completed")
        return self._mutate_and_persist(task, lambda: setattr(task, "status", TaskStatus.READY))

    def start(self, task_id: str) -> Task:
        task = self._require(task_id)
        if task.status not in (TaskStatus.PENDING, TaskStatus.READY):
            raise ValueError(f"Invalid transition: {task.status} -> RUNNING")
        if task.status == TaskStatus.PENDING:
            for dependency_id in task.dependencies:
                dependency = self._require(dependency_id)
                if dependency.status != TaskStatus.COMPLETED:
                    raise ValueError("Task dependencies are not completed")
        def mutate() -> None:
            task.attempts += 1
            task.mark_running()
        task = self._mutate_and_persist(task, mutate)
        self._checkpoint(task, event="started")
        return task

    def retry(self, task_id: str) -> Task:
        """Start a bounded recovery attempt from WAITING state."""
        task = self._require(task_id)
        if task.status != TaskStatus.WAITING:
            raise ValueError(f"Invalid transition: {task.status} -> RUNNING")
        contract = task.execution_contract
        if contract is not None and (task.attempts - 1) >= contract.retry_limit:
            raise ValueError("TASK_RETRY_LIMIT_EXCEEDED")
        def mutate() -> None:
            task.attempts += 1
            task.status = TaskStatus.RUNNING
        task = self._mutate_and_persist(task, mutate)
        self._checkpoint(task, event="retry_started", attempt=task.attempts)
        return task

    def resume(self, task_id: str) -> Task:
        task = self._require(task_id)
        if task.status != TaskStatus.WAITING:
            raise ValueError(f"Invalid transition: {task.status} -> RUNNING")
        task = self._mutate_and_persist(
            task, lambda: setattr(task, "status", TaskStatus.RUNNING)
        )
        self._checkpoint(task, event="resumed")
        return task

    def block(self, task_id: str, reason: str) -> Task:
        task = self._require(task_id)
        if task.status != TaskStatus.WAITING:
            raise ValueError(f"Invalid transition: {task.status} -> BLOCKED")
        def mutate() -> None:
            task.status = TaskStatus.BLOCKED
            task.result = {"reason": reason}
        task = self._mutate_and_persist(task, mutate)
        self._checkpoint(task, event="blocked", reason=reason)
        return task

    def wait(self, task_id: str) -> Task:
        """Move an interrupted active task to WAITING atomically."""
        task = self._require(task_id)
        if task.status not in (TaskStatus.RUNNING, TaskStatus.VERIFYING):
            raise ValueError(f"Invalid transition: {task.status} -> WAITING")
        return self._mutate_and_persist(
            task, lambda: setattr(task, "status", TaskStatus.WAITING)
        )

    def begin_verification(self, task_id: str) -> Task:
        task = self._require(task_id)
        task = self._mutate_and_persist(task, task.mark_verifying)
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
                expected_attempt_id = f"{task_id}:attempt:{task.attempts}"
                if evidence.get("attempt_id") != expected_attempt_id:
                    raise ValueError(
                        f"Verification evidence belongs to another attempt: {evidence_id}"
                    )
                if not evidence["success"]:
                    raise ValueError(f"Verification evidence is unsuccessful: {evidence_id}")
        task = self._mutate_and_persist(
            task, lambda: task.complete(verification)
        )
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
        def mutate() -> None:
            task.status = TaskStatus.FAILED
            task.result = {"reason": reason}
        task = self._mutate_and_persist(task, mutate)
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

    def _mutate_and_persist(self, task: Task, mutator) -> Task:
        """Apply an in-memory mutation only if its durable task write succeeds."""
        snapshot = deepcopy(task)
        try:
            mutator()
            self._persist(task)
        except Exception:
            task.__dict__.clear()
            task.__dict__.update(snapshot.__dict__)
            raise
        return task

    @staticmethod
    def _task_persistence_payload(task: Task, *, result=None) -> dict:
        if result is None:
            result = task.result
        if isinstance(result, VerificationResult):
            result = {
                "type": "VerificationResult",
                "status": result.status.value,
                "reason": result.reason,
                "evidence_ids": list(result.evidence_ids),
                "authority": result.authority,
            }
        return {
            "title": task.title,
            "dependencies": list(task.dependencies),
            "tool_calls": task.tool_calls,
            "execution_contract": (
                task.execution_contract.to_dict()
                if task.execution_contract is not None
                else None
            ),
            "result": result,
        }

    @staticmethod
    def task_persistence_record(task: Task) -> tuple[str, int, dict]:
        return task.status.value, task.attempts, TaskManager._task_persistence_payload(task)

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
            payload=self._task_persistence_payload(task, result=result),
        )

    def _checkpoint(self, task: Task, **payload) -> None:
        if self.checkpoints is not None:
            self.checkpoints.capture(task, **payload)
