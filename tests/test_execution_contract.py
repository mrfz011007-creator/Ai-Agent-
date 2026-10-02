from core.execution_contract import ExecutionContract, ExecutionContractError
from core.contracts import Task, TaskStatus, ToolRequest
from core.runtime import AgentRuntime


def test_execution_contract_rejects_unbounded_or_empty_definition():
    try:
        ExecutionContract(objective="", allowed_tools=("read",))
        assert False, "empty objective must fail"
    except ExecutionContractError:
        pass

    try:
        ExecutionContract(
            objective="read project",
            allowed_tools=("read",),
            evidence_required=True,
            completion_conditions=(),
        )
        assert False, "evidence-backed contract must define completion conditions"
    except ExecutionContractError:
        pass


def test_task_execution_contract_allows_declared_tool_only(tmp_path):
    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        Task(
            "EC-TOOL-1",
            "read bounded data",
            status=TaskStatus.READY,
            execution_contract=ExecutionContract(
                objective="read bounded data",
                allowed_tools=("search_memory",),
                allowed_capabilities=("workspace.read",),
                completion_conditions=("search returns evidence",),
            ),
        )
    )
    runtime.task_manager.start(task.task_id)

    denied = runtime.execute_with_recovery(
        "remember",
        {"key": "x", "value": "y"},
        source="model",
        task_id=task.task_id,
    )
    assert not denied.success
    assert denied.status == "contract_denied"

    allowed = runtime.execute_with_recovery(
        "search_memory",
        {"query": "bounded"},
        source="model",
        task_id=task.task_id,
    )
    assert allowed.success
    assert allowed.evidence_id is not None


def test_execution_contract_survives_task_restart(tmp_path):
    db = tmp_path / "state.sqlite3"
    runtime = AgentRuntime.create(db)
    contract = ExecutionContract(
        objective="read project",
        allowed_tools=("search_memory",),
        max_tool_calls=3,
        retry_limit=1,
        completion_conditions=("successful evidence exists",),
    )
    task = runtime.task_manager.create(
        Task("EC-PERSIST-1", "persist contract", status=TaskStatus.READY, execution_contract=contract)
    )

    restarted = AgentRuntime.create(db)
    restored = restarted.task_manager.restore(task.task_id)

    assert restored is not None
    assert restored.execution_contract == contract


def test_tool_without_declared_capability_is_denied(tmp_path):
    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(
        Task(
            "EC-CAP-1",
            "use a tool with missing capability declaration",
            status=TaskStatus.READY,
            execution_contract=ExecutionContract(
                objective="use a tool with missing capability declaration",
                allowed_tools=("unregistered_capability_tool",),
                allowed_capabilities=("workspace.read",),
                completion_conditions=("tool execution evidence exists",),
            ),
        )
    )
    runtime.task_manager.start(task.task_id)
    runtime.tool_router._registry_getter = lambda name: {
        "func": lambda: {"success": True},
        "permission": "safe",
        "parameters": {"type": "object", "properties": {}},
    } if name == "unregistered_capability_tool" else None

    result = runtime.execute_with_recovery(
        "unregistered_capability_tool", {}, source="model", task_id=task.task_id
    )
    assert not result.success
    assert result.status == "capability_denied"


def test_execution_contract_enforces_tool_call_limit(tmp_path):
    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task(
        "EC-LIMIT-1", "bounded calls", status=TaskStatus.READY,
        execution_contract=ExecutionContract(
            objective="bounded calls", allowed_tools=("search_memory",),
            allowed_capabilities=("workspace.read",), max_tool_calls=1,
            completion_conditions=("one evidence exists",),
        ),
    ))
    runtime.task_manager.start(task.task_id)
    first = runtime.execute_with_recovery("search_memory", {"query": "x"}, task_id=task.task_id)
    second = runtime.execute_with_recovery("search_memory", {"query": "x"}, task_id=task.task_id)
    assert first.success
    assert not second.success
    assert second.status == "task_limit_exceeded"


def test_execution_contract_enforces_retry_limit(tmp_path):
    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task(
        "EC-RETRY-1", "bounded retry", status=TaskStatus.READY,
        execution_contract=ExecutionContract(
            objective="bounded retry", allowed_tools=("run_command",),
            allowed_capabilities=("process.execute",), retry_limit=0,
            completion_conditions=("successful command evidence exists",),
        ),
    ))
    runtime.task_manager.start(task.task_id)
    decision = runtime.recovery_manager.retry_after_failure(
        task.task_id, status="timeout", error="timeout", idempotent=True
    )
    assert decision.action == "NO_RETRY"
    assert "retry limit" in decision.reason.lower()
