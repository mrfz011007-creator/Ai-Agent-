"""Stage 6: deterministic failure injection for recovery and safe-failure paths."""

import pytest

from core.budget import BudgetManager
from core.contracts import Budget, Task, TaskStatus, ToolResult, VerificationResult, VerificationStatus
from core.execution_contract import ExecutionContract
from core.model_gateway import Credential, ModelGateway, ProviderErrorKind
from core.plan import Planner
from core.recovery import RecoveryManager
from core.runtime import AgentRuntime
from core.state_store import StateStore
from core.task_manager import TaskManager
from core.checkpoint import CheckpointManager


def _contract(*, retry_limit=0):
    return ExecutionContract(
        objective="failure injection",
        allowed_tools=("run_command",),
        allowed_capabilities=("process.execute",),
        max_tool_calls=3,
        retry_limit=retry_limit,
        evidence_required=True,
        completion_conditions=({"type": "evidence_success", "task_id": "task"},),
    )


def test_provider_timeout_is_bounded_and_consumes_one_model_call(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []

    class Provider:
        name = "openrouter"

        def text(self, secret, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("network timed out")

    budget = BudgetManager(Budget(max_model_calls=2))
    gateway = ModelGateway(
        credentials=[Credential("OPENROUTER_API_KEY", "OPENROUTER_API_KEY")],
        provider=Provider(),
        budget=budget,
        max_attempts=1,
    )

    with pytest.raises(RuntimeError, match="MODEL_CREDENTIALS_EXHAUSTED"):
        gateway.generate_text("prompt")

    assert len(calls) == 1
    assert budget.budget.model_calls == 1
    assert gateway.credentials[0].state.value == "COOLDOWN"
    assert ModelGateway.classify_error(TimeoutError("network timed out")) == ProviderErrorKind.UNAVAILABLE


def test_malformed_model_proposal_fails_task_without_calling_executor(tmp_path):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    proposal = Planner().propose("malformed model response", [Task("malformed", "Inspect")])
    _, graph = runtime.orchestrator.materialize(proposal, "malformed-plan")
    executions = []
    verifications = []

    task = runtime.orchestrator.execute_model_step(
        graph,
        model_call=lambda _prompt: "this is not JSON",
        execute_proposal=lambda *args, **kwargs: executions.append((args, kwargs)),
        verify_execution=lambda **kwargs: verifications.append(kwargs),
    )

    assert task.status == TaskStatus.FAILED
    assert task.attempts == 1
    assert executions == []
    assert verifications == []
    checkpoint = runtime.state_store.load_latest_checkpoint("malformed")
    assert checkpoint["payload"]["event"] == "failed"


def test_tool_failure_records_failed_evidence_and_never_replays_non_idempotent_command(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    runtime = AgentRuntime.create(state_path=workspace / "state.sqlite3")
    runtime.tool_router._confirmation = lambda *_args: True
    task = runtime.task_manager.create(
        Task("task", "Run a command", execution_contract=_contract())
    )
    runtime.task_manager.start(task.task_id)

    result = runtime.execute_with_recovery(
        "run_command",
        {"command": "false", "cwd": str(workspace), "timeout": 5},
        source="model",
        task_id="task",
        attempt_id="task:attempt:1",
    )

    assert result.success is False
    assert result.evidence_id
    current = runtime.task_manager.get("task")
    assert current.status == TaskStatus.RUNNING
    assert current.attempts == 1
    assert current.tool_calls == 1
    rows = runtime.state_store.load_evidence_for_task("task")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["attempt_id"] == "task:attempt:1"


def test_successful_tool_with_failed_verification_cannot_complete_task(tmp_path):
    runtime = AgentRuntime.create(state_path=tmp_path / "state.sqlite3")
    proposal = Planner().propose("wrong output", [Task("wrong", "Return correct output")])
    _, graph = runtime.orchestrator.materialize(proposal, "wrong-output-plan")
    task = runtime.orchestrator.execute_model_step(
        graph,
        model_call=lambda _prompt: '{"tool":"run_command","action":"execute","arguments":{"command":"true","cwd":"."}}',
        execute_proposal=lambda *args, **kwargs: ToolResult(
            True, "SUCCESS", "run_command", evidence_id="E-WRONG"
        ),
        verify_execution=lambda **kwargs: VerificationResult(
            VerificationStatus.FAILED,
            "Observed output did not satisfy the expected result",
            evidence_ids=("E-WRONG",),
            authority="acceptance_gate",
        ),
    )

    assert task.status == TaskStatus.FAILED
    assert task.result["reason"] == "Observed output did not satisfy the expected result"
    assert task.status != TaskStatus.COMPLETED


def test_interrupted_task_is_not_replayed_after_runtime_restart(tmp_path):
    state_path = tmp_path / "state.sqlite3"
    runtime = AgentRuntime.create(state_path=state_path)
    proposal = Planner().propose("interrupted task", [Task("interrupted", "May have side effects")])
    plan, _ = runtime.orchestrator.materialize(proposal, "interrupted-plan")
    runtime.task_manager.start("interrupted")

    restarted = AgentRuntime.create(state_path=state_path)
    model_calls = []
    restarted.model_gateway.text = lambda *args, **kwargs: model_calls.append(args) or "{}"

    restored_plan, graph = restarted.resume_goal(plan.plan_id)

    assert restored_plan.status.value == "WAITING"
    assert graph.tasks["interrupted"].status == TaskStatus.WAITING
    assert graph.tasks["interrupted"].attempts == 1
    assert model_calls == []
    checkpoint = restarted.state_store.load_latest_checkpoint("interrupted")
    assert checkpoint["payload"]["event"] == "recovery_required"


def test_idempotent_retry_is_limited_by_contract_and_persistent_budget(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    budget = BudgetManager(
        Budget(max_recovery_cycles=3),
        state_store=store,
    )
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    recovery = RecoveryManager(manager, budget=budget)
    task = manager.create(Task("task", "Retry safely", execution_contract=_contract(retry_limit=1)))
    manager.start(task.task_id)

    first = recovery.retry_after_failure(
        "task", status="TIMEOUT", error="network timed out", idempotent=True
    )
    assert first.action == "RETRY"
    assert manager.get("task").status == TaskStatus.RUNNING
    assert manager.get("task").attempts == 2
    assert budget.budget.recovery_cycles == 1

    second = recovery.retry_after_failure(
        "task", status="TIMEOUT", error="network timed out again", idempotent=True
    )
    assert second.action == "NO_RETRY"
    assert manager.get("task").attempts == 2
    assert budget.budget.recovery_cycles == 1


def test_failed_recovery_persistence_rolls_back_task_and_budget(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.sqlite3")
    budget = BudgetManager(Budget(max_recovery_cycles=2), state_store=store)
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    recovery = RecoveryManager(manager, budget=budget)
    task = manager.create(Task("task", "Persistence failure", execution_contract=_contract(retry_limit=1)))
    manager.start(task.task_id)
    original_save = store.save_task

    def fail_task_write(*args, **kwargs):
        raise OSError("injected disk write failure")

    monkeypatch.setattr(store, "save_task", fail_task_write)

    with pytest.raises(OSError, match="injected disk write failure"):
        recovery.retry_after_failure(
            "task", status="TIMEOUT", error="temporary network failure", idempotent=True
        )

    current = manager.get("task")
    persisted = store.load_task("task")
    assert current.status == TaskStatus.RUNNING
    assert current.attempts == 1
    assert persisted["status"] == TaskStatus.RUNNING.value
    assert persisted["attempts"] == 1
    assert budget.budget.recovery_cycles == 0

    monkeypatch.setattr(store, "save_task", original_save)
    restarted = AgentRuntime.create(state_path=store.path)
    assert restarted.task_manager.restore("task").status == TaskStatus.RUNNING
