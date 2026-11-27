from __future__ import annotations

import math
import os
import uuid
from typing import Callable

from core.budget import BudgetManager
from core.contracts import Decision, ToolRequest, ToolResult
from security.policy import PolicyContext, PolicyEngine
from verification.evidence import EvidenceStore
from security.guard import GuardEngine
from security.capabilities import CapabilityPolicy, capabilities_for_tool
from security.schema import SchemaValidationError, validate_tool_arguments
from security.redaction import redact_text, redact_value


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
        task_getter: Callable[[str], object | None] | None = None,
        capability_policy: CapabilityPolicy | None = None,
        task_tool_call_consumer: Callable[[str], object] | None = None,
    ):
        self._registry_getter = registry_getter
        self._policy = policy
        self._budget = budget
        self._evidence = evidence
        self._confirmation = confirmation
        workspace_root = os.environ.get("AI_AGENT_WORKSPACE_ROOT")
        self._workspace_configured = bool(workspace_root and workspace_root.strip()) or guard is not None
        self._guard = guard or GuardEngine(workspace_root or "/__ai_agent_workspace_not_configured__")
        self._task_getter = task_getter
        self._capability_policy = capability_policy
        self._task_tool_call_consumer = task_tool_call_consumer

    def _deny(self, request: ToolRequest, status: str, error: str) -> ToolResult:
        """Persist a redacted audit record for security-relevant denials."""
        result = ToolResult(
            False,
            status,
            request.tool,
            error=redact_text(str(error)),
        )
        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"
        try:
            self._evidence.record(
                evidence_id=evidence_id,
                task_id=request.task_id,
                attempt_id=request.attempt_id,
                tool=request.tool,
                action=request.action,
                result=result,
            )
        except Exception:
            # A logging failure must never turn a denied request into execution.
            return result
        result.evidence_id = evidence_id
        return result

    def execute(self, request: ToolRequest) -> ToolResult:
        if request.trusted_tool is not None:
            if request.tool != request.trusted_tool:
                return self._deny(
                    request,
                    "instruction_mismatch",
                    f"TRUSTED_INSTRUCTION_MISMATCH: expected tool {request.trusted_tool}",
                )
            if request.trusted_arguments is None:
                return self._deny(
                    request,
                    "instruction_mismatch",
                    "TRUSTED_INSTRUCTION_CONSTRAINT_MISSING",
                )
            actual_arguments = dict(request.arguments)
            expected_arguments = dict(request.trusted_arguments)
            if actual_arguments != expected_arguments:
                changed_keys = sorted(
                    key for key in set(actual_arguments) | set(expected_arguments)
                    if actual_arguments.get(key) != expected_arguments.get(key)
                    or (key in actual_arguments) != (key in expected_arguments)
                )
                return self._deny(
                    request,
                    "instruction_mismatch",
                    "TRUSTED_INSTRUCTION_MISMATCH: exact arguments required; "
                    f"mismatched keys: {', '.join(changed_keys) or 'unknown'}",
                )

        metadata = self._registry_getter(request.tool)
        if metadata is None:
            return self._deny(request, "denied", "Unknown tool")
        if request.tool == "run_command" and not self._workspace_configured:
            return self._deny(request, "guard_denied", "WORKSPACE_NOT_CONFIGURED")

        contract = None
        if self._task_getter is not None and request.task_id is not None:
            task = self._task_getter(request.task_id)
            if task is None:
                return ToolResult(
                    False,
                    "task_not_found",
                    request.tool,
                    error=f"Unknown task: {request.task_id}",
                )
            # A task-scoped tool call is executable only while the task owns
            # the execution phase. This prevents callers from performing new
            # side effects after completion, during verification, or while
            # waiting for reconciliation.
            task_status = getattr(task, "status", None)
            # Lifecycle state is the outer execution boundary. Only a task
            # that currently owns the execution phase may reach attempt
            # binding; this preserves the stable task_not_executable result
            # for terminal/waiting/verification states.
            if getattr(task_status, "value", task_status) != "RUNNING":
                return ToolResult(
                    False,
                    "task_not_executable",
                    request.tool,
                    error=f"Task is not executable in status: {task_status}",
                )
            expected_attempt_id = f"{request.task_id}:attempt:{getattr(task, 'attempts', 0)}"
            if request.attempt_id != expected_attempt_id:
                return ToolResult(
                    False,
                    "attempt_mismatch",
                    request.tool,
                    error=f"Tool request is not bound to current task attempt: {expected_attempt_id}",
                )
            contract = getattr(task, "execution_contract", None)
            if contract is not None and not contract.allows_tool(request.tool):
                return self._deny(
                    request, "contract_denied",
                    f"Tool not allowed by execution contract: {request.tool}",
                )

        if contract is not None and not contract.allows_capabilities(capabilities_for_tool(metadata)):
            return self._deny(
                request, "contract_capability_denied",
                f"Tool capabilities exceed execution contract: {request.tool}",
            )

        if self._capability_policy is not None:
            capability = self._capability_policy.decide(request.tool, metadata)
            if not capability.allowed:
                return self._deny(request, "capability_denied", capability.reason)

        arguments = dict(request.arguments)
        if request.tool in {"run_command", "jalankan_python"}:
            remaining_runtime = self._budget.remaining_runtime_seconds
            if remaining_runtime <= 0:
                return self._deny(request, "budget_exceeded", "RUNTIME_BUDGET_EXCEEDED")
            max_timeout = 120.0 if request.tool == "run_command" else 30.0
            default_timeout = (
                remaining_runtime if request.tool == "run_command"
                else min(15.0, remaining_runtime)
            )
            timeout = arguments.get("timeout", default_timeout)
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                return self._deny(request, "schema_invalid", "INVALID_TIMEOUT")
            arguments["timeout"] = min(float(timeout), remaining_runtime, max_timeout)

        schema = metadata.get("parameters")
        if schema is not None:
            try:
                validate_tool_arguments(schema, arguments)
            except SchemaValidationError as error:
                return self._deny(request, "schema_invalid", str(error))

        # Verified file creation must contain meaningful content. Enforce this
        # at the execution boundary, before guard/policy checks and approval.
        if request.tool == "buat_file_terverifikasi":
            content = arguments.get("isi")
            if isinstance(content, str) and not content.strip():
                return self._deny(
                    request, "schema_invalid", "WHITESPACE_ONLY: isi"
                )

        # Run security inspection before asking for approval so a disallowed
        # process request can never be approved accidentally.
        guard = self._guard.check(tool=request.tool, arguments=arguments)
        if not guard.allowed:
            return self._deny(request, "guard_denied", guard.reason)

        policy = self._policy.decide(
            PolicyContext(
                tool=request.tool,
                action=request.action,
                arguments=arguments,
                source=request.source,
                task_id=request.task_id,
            )
        )

        if policy.decision == Decision.DENY:
            return self._deny(request, "denied", policy.reason)

        if policy.decision == Decision.ASK:
            if self._confirmation is None or not self._confirmation(
                request.tool, arguments
            ):
                return self._deny(request, "cancelled", "User denied tool execution")

        # Check the task-local quota without mutating it. Mutation is deferred
        # until the handler exists and the global budget has been reserved.
        if (
            self._task_tool_call_consumer is not None
            and request.task_id is not None
            and contract is not None
            and getattr(task, "tool_calls", 0) >= contract.max_tool_calls
        ):
            return self._deny(request, "task_limit_exceeded", "TASK_TOOL_CALL_LIMIT_EXCEEDED")

        function = metadata.get("func")
        if function is None:
            return self._deny(request, "error", "Tool has no handler")

        try:
            self._budget.reserve_tool_call()
        except RuntimeError as error:
            return self._deny(request, "budget_exceeded", str(error))

        if self._task_tool_call_consumer is not None and request.task_id is not None:
            try:
                self._task_tool_call_consumer(request.task_id)
            except RuntimeError as error:
                return self._deny(request, "task_limit_exceeded", str(error))

        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"

        try:
            data = function(**arguments)
            safe_data = redact_value(data)
            max_output = self._budget.budget.max_output_chars
            remaining = max_output
            output_truncated = False
            max_items = 1000

            def bound(value):
                nonlocal remaining, output_truncated
                if remaining <= 0:
                    output_truncated = True
                    return "[OUTPUT_TRUNCATED]"
                if isinstance(value, str):
                    if len(value) > remaining:
                        output_truncated = True
                        result = value[:remaining]
                        remaining = 0
                        return result
                    remaining -= len(value)
                    return value
                if isinstance(value, dict):
                    result = {}
                    for index, (key, item) in enumerate(value.items()):
                        if index >= max_items or remaining <= 0:
                            output_truncated = True
                            break
                        bounded = bound(item)
                        result[key] = bounded
                    if len(value) > max_items:
                        output_truncated = True
                    return result
                if isinstance(value, list):
                    result = []
                    for index, item in enumerate(value):
                        if index >= max_items or remaining <= 0:
                            output_truncated = True
                            break
                        result.append(bound(item))
                    if len(value) > max_items:
                        output_truncated = True
                    return result
                if isinstance(value, tuple):
                    if len(value) > max_items:
                        output_truncated = True
                    return tuple(bound(item) for item in value[:max_items])
                return value

            safe_data = bound(safe_data)
            if isinstance(safe_data, dict) and output_truncated:
                safe_data["output_truncated"] = True

            if isinstance(safe_data, dict) and "success" in safe_data:
                result = ToolResult(
                    bool(safe_data.get("success")),
                    str(safe_data.get("status", "success" if safe_data.get("success") else "error")).lower(),
                    request.tool,
                    data=safe_data,
                    error=None if safe_data.get("success") else redact_text(str(
                        (f"{safe_data.get('code')}: " if safe_data.get("code") else "")
                        + str(
                            safe_data.get("stderr")
                            or safe_data.get("error")
                            or safe_data.get("pesan")
                            or "Tool returned success=false without an error message"
                        )
                    )),
                )
            elif isinstance(data, str) and len(data) > self._budget.budget.max_output_chars:
                data = safe_data[: self._budget.budget.max_output_chars]
                result = ToolResult(
                    True,
                    "success",
                    request.tool,
                    data=data,
                    error="OUTPUT_TRUNCATED",
                )
            else:
                result = ToolResult(True, "success", request.tool, data=safe_data)
        except Exception as error:
            result = ToolResult(False, "error", request.tool, error=redact_text(str(error)))

        self._evidence.record(
            evidence_id=evidence_id,
            task_id=request.task_id,
            attempt_id=request.attempt_id,
            tool=request.tool,
            action=request.action,
            result=result,
            kind="execution",
        )

        result.evidence_id = evidence_id
        return result