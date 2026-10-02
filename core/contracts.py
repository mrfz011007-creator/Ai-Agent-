from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Decision(str, Enum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


class VerificationStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ToolRequest:
    tool: str
    action: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    source: str = "agent"
    task_id: str | None = None
    attempt_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class PermissionDecision:
    decision: Decision
    reason: str
    risk: str = "LOW"


@dataclass
class ToolResult:
    success: bool
    status: str
    tool: str
    data: Any = None
    error: str | None = None
    evidence_id: str | None = None


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    task_id: str | None
    tool: str
    action: str
    attempt_id: str | None = None
    success: bool
    result_status: str
    error: str | None = None


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    task_id: str
    attempt_id: str | None
    path: str
    kind: str
    sha256: str
    size: int
    source_commit: str | None = None
    evidence_id: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class BuildResult:
    success: bool
    command: str
    exit_code: int | None
    artifact_ids: tuple[str, ...] = ()
    evidence_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TestResult:
    success: bool
    command: str
    exit_code: int | None
    evidence_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    reason: str
    evidence_ids: tuple[str, ...] = ()


@dataclass
class Budget:
    max_tool_calls: int = 50
    max_model_calls: int = 20
    max_recovery_cycles: int = 3
    max_runtime_seconds: float = 900.0
    max_output_chars: int = 100_000
    tool_calls: int = 0
    model_calls: int = 0
    recovery_cycles: int = 0

    def can_use_tool(self) -> bool:
        return self.tool_calls < self.max_tool_calls

    def consume_tool(self) -> None:
        if not self.can_use_tool():
            raise RuntimeError("TOOL_BUDGET_EXCEEDED")
        self.tool_calls += 1

    def can_use_model(self) -> bool:
        return self.model_calls < self.max_model_calls

    def consume_model(self) -> None:
        if not self.can_use_model():
            raise RuntimeError("MODEL_BUDGET_EXCEEDED")
        self.model_calls += 1

    def can_recover(self) -> bool:
        return self.recovery_cycles < self.max_recovery_cycles

    def consume_recovery(self) -> None:
        if not self.can_recover():
            raise RuntimeError("RECOVERY_BUDGET_EXCEEDED")
        self.recovery_cycles += 1


@dataclass
class Task:
    task_id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[str] = field(default_factory=list)
    attempts: int = 0
    result: Any = None

    def mark_running(self) -> None:
        if self.status not in (TaskStatus.READY, TaskStatus.PENDING):
            raise ValueError(f"Invalid transition: {self.status} -> RUNNING")
        self.status = TaskStatus.RUNNING

    def mark_verifying(self) -> None:
        if self.status != TaskStatus.RUNNING:
            raise ValueError(f"Invalid transition: {self.status} -> VERIFYING")
        self.status = TaskStatus.VERIFYING

    def complete(self, verification: VerificationResult) -> None:
        if self.status != TaskStatus.VERIFYING:
            raise ValueError(f"Invalid transition: {self.status} -> COMPLETED")
        if verification.status != VerificationStatus.PASSED:
            raise ValueError("COMPLETED requires VerificationStatus.PASSED")
        if not verification.evidence_ids:
            raise ValueError("COMPLETED requires evidence-backed verification")
        self.status = TaskStatus.COMPLETED
        self.result = verification
