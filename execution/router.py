from __future__ import annotations

import uuid
from typing import Callable

from core.budget import BudgetManager
from core.contracts import Decision, ToolRequest, ToolResult
from security.policy import PolicyContext, PolicyEngine
from verification.evidence import EvidenceStore
from security.guard import GuardEngine


class ToolRouter:
    """Single execution boundary for local and model-originated tool requests."""

    def __init__(
        self,
        *,
        registry_getter: Callable,
        policy: PolicyEngine,
        budget: BudgetManager,
        evidence: EvidenceStore,
        confirmation: Callable[[str, dict], bool] | None = None,
        guard: GuardEngine | None = None,
    ):
        self._registry_getter = registry_getter
        self._policy = policy
        self._budget = budget
        self._evidence = evidence
        self._confirmation = confirmation
        self._guard = guard

    def execute(self, request: ToolRequest) -> ToolResult:
        policy = self._policy.decide(
            PolicyContext(
                tool=request.tool,
                action=request.action,
                arguments=request.arguments,
                source=request.source,
                task_id=request.task_id,
            )
        )

        if policy.decision == Decision.DENY:
            return ToolResult(False, "denied", request.tool, error=policy.reason)

        if policy.decision == Decision.ASK:
            if self._confirmation is None or not self._confirmation(
                request.tool, dict(request.arguments)
            ):
                return ToolResult(
                    False,
                    "cancelled",
                    request.tool,
                    error="User denied tool execution",
                )

        if self._guard is not None:
            guard = self._guard.check(
                tool=request.tool,
                arguments=dict(request.arguments),
            )
            if not guard.allowed:
                return ToolResult(
                    False,
                    "guard_denied",
                    request.tool,
                    error=guard.reason,
                )

        try:
            self._budget.reserve_tool_call()
        except RuntimeError as error:
            return ToolResult(
                False,
                "budget_exceeded",
                request.tool,
                error=str(error),
            )

        metadata = self._registry_getter(request.tool)
        if metadata is None:
            return ToolResult(False, "error", request.tool, error="Unknown tool")

        function = metadata.get("func")
        if function is None:
            return ToolResult(False, "error", request.tool, error="Tool has no handler")

        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"

        try:
            data = function(**dict(request.arguments))
            if isinstance(data, dict) and "success" in data:
                result = ToolResult(
                    bool(data.get("success")),
                    str(data.get("status", "success" if data.get("success") else "error")).lower(),
                    request.tool,
                    data=data,
                    error=None if data.get("success") else str(
                        data.get("stderr") or data.get("error") or "Command failed"
                    ),
                )
            elif isinstance(data, str) and len(data) > self._budget.budget.max_output_chars:
                data = data[: self._budget.budget.max_output_chars]
                result = ToolResult(
                    True,
                    "success",
                    request.tool,
                    data=data,
                    error="OUTPUT_TRUNCATED",
                )
            else:
                result = ToolResult(True, "success", request.tool, data=data)
        except Exception as error:
            result = ToolResult(False, "error", request.tool, error=str(error))

        self._evidence.record(
            evidence_id=evidence_id,
            task_id=request.task_id,
            tool=request.tool,
            action=request.action,
            result=result,
        )

        result.evidence_id = evidence_id
        return result
