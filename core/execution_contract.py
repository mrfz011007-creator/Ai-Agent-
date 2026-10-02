from __future__ import annotations

from dataclasses import dataclass


class ExecutionContractError(ValueError):
    """Raised when a task execution contract is invalid."""


@dataclass(frozen=True)
class ExecutionContract:
    """Bounded execution rules attached to one task."""

    objective: str
    allowed_tools: tuple[str, ...]
    max_tool_calls: int = 10
    retry_limit: int = 0
    evidence_required: bool = True
    completion_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ExecutionContractError("Execution objective cannot be empty")
        if not self.allowed_tools:
            raise ExecutionContractError("Execution contract must allow at least one tool")
        if any(not tool.strip() for tool in self.allowed_tools):
            raise ExecutionContractError("Execution contract contains an empty tool name")
        if self.max_tool_calls < 1:
            raise ExecutionContractError("max_tool_calls must be at least 1")
        if self.retry_limit < 0:
            raise ExecutionContractError("retry_limit cannot be negative")
        if self.evidence_required and not self.completion_conditions:
            raise ExecutionContractError(
                "Evidence-backed contracts require completion conditions"
            )

    def allows_tool(self, tool: str) -> bool:
        return tool in self.allowed_tools

    def to_dict(self) -> dict:
        return {
            "type": "ExecutionContract",
            "objective": self.objective,
            "allowed_tools": list(self.allowed_tools),
            "max_tool_calls": self.max_tool_calls,
            "retry_limit": self.retry_limit,
            "evidence_required": self.evidence_required,
            "completion_conditions": list(self.completion_conditions),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ExecutionContract":
        if not isinstance(payload, dict):
            raise ExecutionContractError("Execution contract payload must be an object")
        return cls(
            objective=payload["objective"],
            allowed_tools=tuple(payload["allowed_tools"]),
            max_tool_calls=int(payload.get("max_tool_calls", 10)),
            retry_limit=int(payload.get("retry_limit", 0)),
            evidence_required=bool(payload.get("evidence_required", True)),
            completion_conditions=tuple(payload.get("completion_conditions", ())),
        )
