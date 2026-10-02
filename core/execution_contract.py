from __future__ import annotations

from dataclasses import dataclass\nfrom typing import Mapping
from security.capabilities import Capability


class ExecutionContractError(ValueError):
    """Raised when a task execution contract is invalid."""


@dataclass(frozen=True)
class ExecutionContract:
    """Bounded execution rules attached to one task."""

    objective: str
    allowed_tools: tuple[str, ...]
    allowed_capabilities: tuple[str, ...] = ()
    max_tool_calls: int = 10
    retry_limit: int = 0
    evidence_required: bool = True
    completion_conditions: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ExecutionContractError("Execution objective cannot be empty")
        if not self.allowed_tools:
            raise ExecutionContractError("Execution contract must allow at least one tool")
        if any(not tool.strip() for tool in self.allowed_tools):
            raise ExecutionContractError("Execution contract contains an empty tool name")
        try:
            tuple(Capability(value) for value in self.allowed_capabilities)
        except ValueError as error:
            raise ExecutionContractError(f"Unknown execution capability: {error}") from error
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

    def allows_capabilities(self, capabilities: tuple[Capability, ...]) -> bool:
        if not self.allowed_capabilities:
            return False
        return all(capability.value in self.allowed_capabilities for capability in capabilities)

    def to_dict(self) -> dict:
        return {
            "type": "ExecutionContract",
            "objective": self.objective,
            "allowed_tools": list(self.allowed_tools),
            "allowed_capabilities": list(self.allowed_capabilities),
            "max_tool_calls": self.max_tool_calls,
            "retry_limit": self.retry_limit,
            "evidence_required": self.evidence_required,
            "completion_conditions": list(self.completion_conditions),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ExecutionContract":
        if not isinstance(payload, dict):
            raise ExecutionContractError("Execution contract payload must be an object")

        objective = payload.get("objective")
        allowed_tools = payload.get("allowed_tools")
        allowed_capabilities = payload.get("allowed_capabilities", ())
        completion_conditions = payload.get("completion_conditions", ())
        max_tool_calls = payload.get("max_tool_calls", 10)
        retry_limit = payload.get("retry_limit", 0)
        evidence_required = payload.get("evidence_required", True)

        if not isinstance(objective, str):
            raise ExecutionContractError("Execution contract objective must be a string")
        if not isinstance(allowed_tools, (list, tuple)) or not all(
            isinstance(tool, str) for tool in allowed_tools
        ):
            raise ExecutionContractError("Execution contract allowed_tools must be strings")
        if not isinstance(allowed_capabilities, (list, tuple)) or not all(
            isinstance(capability, str) for capability in allowed_capabilities
        ):
            raise ExecutionContractError(
                "Execution contract allowed_capabilities must be strings"
            )
        if not isinstance(completion_conditions, (list, tuple)) or not all(
            isinstance(condition, str) for condition in completion_conditions
        ):
            raise ExecutionContractError(
                "Execution contract completion_conditions must be strings"
            )
        if isinstance(max_tool_calls, bool) or not isinstance(max_tool_calls, int):
            raise ExecutionContractError("max_tool_calls must be an integer")
        if isinstance(retry_limit, bool) or not isinstance(retry_limit, int):
            raise ExecutionContractError("retry_limit must be an integer")
        if not isinstance(evidence_required, bool):
            raise ExecutionContractError("evidence_required must be a boolean")

        return cls(
            objective=objective,
            allowed_tools=tuple(allowed_tools),
            allowed_capabilities=tuple(allowed_capabilities),
            max_tool_calls=max_tool_calls,
            retry_limit=retry_limit,
            evidence_required=evidence_required,
            completion_conditions=tuple(completion_conditions),
        )
