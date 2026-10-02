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
