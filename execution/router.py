from __future__ import annotations

import hashlib
import json
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
        task_tool_call_refunder: Callable[[str], object] | None = None,
    ):
        self._registry_getter = registry_getter
        self._policy = policy
        self._budget = budget
        self._evidence = evidence
        self._confirmation = confirmation
        self._guard = guard
        self._task_getter = task_getter
        self._capability_policy = capability_policy
        self._task_tool_call_consumer = task_tool_call_consumer
        self._task_tool_call_refunder = task_tool_call_refunder

    def execute(self, request: ToolRequest) -> ToolResult:
        metadata = self._registry_getter(request.tool)
        if metadata is None:
            return ToolResult(False, "denied", request.tool, error="Unknown tool")

        contract = None
        if self._task_getter is not None and request.task_id is not None:
            task = self._task_getter(request.task_id)
            contract = getattr(task, "execution_contract", None) if task is not None else None
            if contract is not None and not contract.allows_tool(request.tool):
                return ToolResult(
                    False,
                    "contract_denied",
                    request.tool,
                    error=f"Tool not allowed by execution contract: {request.tool}",
                )

        if contract is not None and not contract.allows_capabilities(
            capabilities_for_tool(metadata)
        ):
            return ToolResult(
                False,
                "contract_capability_denied",
                request.tool,
                error=f"Tool capabilities exceed execution contract: {request.tool}",
            )

        if self._capability_policy is not None:
            capability = self._capability_policy.decide(request.tool, metadata)
            if not capability.allowed:
                return ToolResult(
                    False,
                    "capability_denied",
                    request.tool,
                    error=capability.reason,
                )

        schema = metadata.get("parameters")
        if schema is not None:
            try:
                validate_tool_arguments(schema, request.arguments)
            except SchemaValidationError as error:
                return ToolResult(False, "schema_invalid", request.tool, error=str(error))

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
                return ToolResult(False, "guard_denied", request.tool, error=guard.reason)

        function = metadata.get("func")
        if function is None:
            return ToolResult(False, "error", request.tool, error="Tool has no handler")

        request_id = request.request_id or f"req-{uuid.uuid4().hex}"
        arguments_hash = hashlib.sha256(
            json.dumps(
                dict(request.arguments),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()

        execution_store = getattr(self._evidence, "store", None)
        if execution_store is not None:
            existing = execution_store.load_tool_execution(request_id)
            if existing is not None:
                if (
                    existing["tool"] != request.tool
                    or existing["action"] != request.action
                    or existing["arguments_hash"] != arguments_hash
                ):
                    return ToolResult(
                        False,
                        "execution_conflict",
                        request.tool,
                        error="request_id is already bound to a different tool operation",
                    )
                if existing["status"] == "COMPLETED":
                    payload = execution_store._decode_tool_execution_result(existing) or {}
                    return ToolResult(
                        bool(payload.get("success")),
                        str(payload.get("status", "error")),
                        request.tool,
                        data=payload.get("data"),
                        error=payload.get("error"),
                        evidence_id=existing.get("evidence_id"),
                    )
                return ToolResult(
                    False,
                    "execution_unknown",
                    request.tool,
                    error=(
                        "Execution already started but durable completion was not "
                        "recorded. Reconciliation is required before retry."
                    ),
                )

            uncertain = execution_store.load_unknown_tool_execution(
                request.task_id,
                request.tool,
                request.action,
            )
            if uncertain is not None:
                return ToolResult(
                    False,
                    "execution_reconciliation_required",
                    request.tool,
                    error=(
                        "A previous execution for this task has unknown completion "
                        f"state. Reconcile request {uncertain['request_id']} first."
                    ),
                )

        try:
            self._budget.reserve_tool_call()
        except RuntimeError as error:
            return ToolResult(False, "budget_exceeded", request.tool, error=str(error))

        task_reserved = False
        if self._task_tool_call_consumer is not None and request.task_id is not None:
            try:
                self._task_tool_call_consumer(request.task_id)
                task_reserved = True
            except RuntimeError as error:
                self._budget.refund_tool_call()
                return ToolResult(
                    False, "task_limit_exceeded", request.tool, error=str(error)
                )

        if execution_store is not None:
            try:
                record = execution_store.begin_tool_execution(
                    request_id=request_id,
                    task_id=request.task_id,
                    attempt_id=request.attempt_id,
                    tool=request.tool,
                    action=request.action,
                    arguments_hash=arguments_hash,
                )
            except Exception:
                if task_reserved and self._task_tool_call_refunder is not None and request.task_id is not None:
                    try:
                        self._task_tool_call_refunder(request.task_id)
                    except Exception:
                        pass
                self._budget.refund_tool_call()
                raise
            if record["status"] != "STARTED":
                if task_reserved and self._task_tool_call_refunder is not None and request.task_id is not None:
                    try:
                        self._task_tool_call_refunder(request.task_id)
                    except Exception:
                        pass
                self._budget.refund_tool_call()
                return ToolResult(
                    False,
                    "execution_unknown",
                    request.tool,
                    error="Execution reservation already exists and requires reconciliation.",
                )

        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"

        try:
            data = function(**dict(request.arguments))
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
                        result[key] = bound(item)
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
                    str(
                        safe_data.get(
                            "status", "success" if safe_data.get("success") else "error"
                        )
                    ).lower(),
                    request.tool,
                    data=safe_data,
                    error=None
                    if safe_data.get("success")
                    else redact_text(
                        str(
                            safe_data.get("stderr")
                            or safe_data.get("error")
                            or "Command failed"
                        )
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
                result = ToolResult(True, "success", request.tool, data=safe_data)
        except Exception as error:
            result = ToolResult(False, "error", request.tool, error=redact_text(str(error)))

        try:
            self._evidence.record(
                evidence_id=evidence_id,
                task_id=request.task_id,
                attempt_id=request.attempt_id,
                tool=request.tool,
                action=request.action,
                result=result,
            )
        except Exception as error:
            if execution_store is not None:
                try:
                    execution_store.finalize_tool_execution(
                        request_id,
                        status="UNKNOWN",
                        result=None,
                        success=None,
                        result_status="EVIDENCE_PERSISTENCE_FAILED",
                        error=redact_text(str(error)),
                        evidence_id=None,
                    )
                except Exception:
                    pass
            return ToolResult(
                False,
                "execution_unknown",
                request.tool,
                error=(
                    "TOOL_EXECUTION_EVIDENCE_PERSISTENCE_FAILED: "
                    + redact_text(str(error))
                ),
            )

        result.evidence_id = evidence_id
        if execution_store is not None:
            payload = {
                "success": result.success,
                "status": result.status,
                "data": result.data,
                "error": result.error,
            }
            try:
                execution_store.finalize_tool_execution(
                    request_id,
                    status="COMPLETED",
                    result=payload,
                    success=result.success,
                    result_status=result.status,
                    error=result.error,
                    evidence_id=evidence_id,
                )
            except Exception:
                return ToolResult(
                    False,
                    "execution_unknown",
                    request.tool,
                    error=(
                        "TOOL_EXECUTION_FINALIZATION_FAILED: durable evidence exists "
                        "but execution completion state could not be finalized."
                    ),
                )

        return result
