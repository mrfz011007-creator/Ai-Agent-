from __future__ import annotations

import json

import pytest

from core.budget import Budget, BudgetManager
from core.contracts import Task, TaskStatus, ToolRequest, ToolResult
from core.execution_contract import ExecutionContract
from core.plan import PlanDecoder, PlanGraphError
from core.recovery import RecoveryManager, ReconcileOutcome
from core.state_store import StateStore
from core.task_manager import TaskManager
from core.checkpoint import CheckpointManager
from execution.router import ToolRouter
from security.policy import PolicyEngine
from verification.artifacts import ArtifactManager
from verification.evidence import EvidenceStore
import intent_router
import memory


def test_model_plan_contract_is_preserved():
    proposal = PlanDecoder.from_mapping({
        "goal": "edit project",
        "tasks": [{
            "task_id": "edit",
            "title": "Edit project",
            "dependencies": [],
            "execution_contract": {
                "objective": "Edit project",
                "allowed_tools": ["patch_file"],
                "allowed_capabilities": ["workspace.write"],
                "max_tool_calls": 2,
                "retry_limit": 0,
                "evidence_required": True,
                "completion_conditions": [{"type": "evidence_success", "task_id": "edit"}],
            },
        }],
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    })
    contract = proposal.tasks[0].execution_contract
    assert contract is not None
    assert contract.allowed_tools == ("patch_file",)
    assert contract.allowed_capabilities == ("workspace.write",)


def test_model_plan_without_contract_falls_back_to_read_only_contract():
    proposal = PlanDecoder.from_mapping({
        "goal": "inspect",
        "tasks": [{"task_id": "inspect", "title": "Inspect project", "dependencies": []}],
        "acceptance_criteria": [{"type": "all_tasks_completed"}],
    })
    contract = proposal.tasks[0].execution_contract
    assert contract is not None
    assert contract.allowed_capabilities == ("workspace.read",)
    assert "run_command" not in contract.allowed_tools


def test_model_plan_rejects_non_object_and_empty_task_list():
    with pytest.raises(PlanGraphError):
        PlanDecoder.from_mapping([])
    with pytest.raises(PlanGraphError):
        PlanDecoder.from_mapping({
            "goal": "empty",
            "tasks": [],
            "acceptance_criteria": [],
        })


def test_negated_local_intent_is_not_executed():
    assert intent_router.deteksi_intent("jangan lihat file") is None
    assert intent_router.deteksi_intent("lihat file")["tool"] == "lihat"


def test_intent_requires_ordered_phrase():
    assert intent_router.deteksi_intent("file yang saya lihat") is None


def test_corrupted_memory_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "memory.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="MEMORY_CORRUPTED_OR_UNREADABLE"):
        memory.load_memory()


def test_router_redacts_long_string_output():
    secret = "api_key=TOPSECRET123456789"
    registry = {
        "text": {
            "permission": "safe",
            "parameters": {"type": "object", "properties": {}},
            "func": lambda: secret * 10,
        }
    }
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=BudgetManager(Budget(max_output_chars=40)),
        evidence=EvidenceStore(),
    )

    result = router.execute(ToolRequest("text", "execute"))
    assert result.success
    assert "TOPSECRET" not in str(result.data)
    assert "REDACTED" in str(result.data)


def test_router_clamps_run_command_timeout_to_remaining_runtime():
    seen = {}
    registry = {
        "run_command": {
            "permission": "safe",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "number"},
                },
                "required": ["command", "cwd"],
            },
            "func": lambda **kwargs: seen.update(kwargs) or {
                "success": True,
                "status": "SUCCESS",
            },
        }
    }
    budget = BudgetManager(Budget(max_runtime_seconds=1.0))
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=budget,
        evidence=EvidenceStore(),
    )

    result = router.execute(
        ToolRequest(
            "run_command",
            "execute",
            {"command": "true", "cwd": ".", "timeout": 999999},
        )
    )
    assert result.success
    assert 0 < seen["timeout"] <= 1.0


def test_safe_reconciliation_rejects_evidence_from_another_task(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    evidence = EvidenceStore(store)
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))

    task_a = manager.create(Task("A", "A"))
    manager.start(task_a.task_id)

    task_b = manager.create(Task("B", "B"))
    manager.start(task_b.task_id)

    recovery = RecoveryManager(manager, evidence)
    recovery.recover_task("A")

    evidence.record(
        evidence_id="EV-B",
        task_id="B",
        tool="inspect",
        action="inspect",
        result=ToolResult(True, "SUCCESS", "inspect"),
    )

    with pytest.raises(ValueError, match="another task"):
        recovery.reconcile(
            "A",
            ReconcileOutcome.SAFE_TO_RESUME,
            "State matches",
            evidence_ids=("EV-B",),
        )


def test_build_artifact_registration_rejects_escape(tmp_path):
    from unittest.mock import Mock
    from verification.build import BuildManager, CommandResult

    artifacts = ArtifactManager(StateStore(tmp_path / "state.sqlite3"))
    router = Mock()
    router.execute.return_value = ToolResult(
        True,
        "success",
        "run_command",
        data={"exit_code": 0, "stdout": "", "stderr": ""},
        evidence_id="EV",
    )
    manager = BuildManager(artifacts, router)

    with pytest.raises(ValueError, match="inside build workspace"):
        manager.build(
            task_id="A",
            command="true",
            cwd=tmp_path,
            artifact_paths=[tmp_path.parent / "outside.apk"],
        )

def test_rejected_schema_call_does_not_consume_task_quota(tmp_path):
    from core.runtime import AgentRuntime

    runtime = AgentRuntime.create(tmp_path / "state.sqlite3")
    task = runtime.task_manager.create(Task(
        "Q", "quota", status=TaskStatus.READY,
        execution_contract=ExecutionContract(
            objective="quota",
            allowed_tools=("search_memory",),
            allowed_capabilities=("workspace.read",),
            max_tool_calls=1,
            completion_conditions=({"type": "evidence_success", "task_id": "Q"},),
        ),
    ))
    runtime.task_manager.start("Q")

    bad = runtime.execute_with_recovery(
        "search_memory", {}, task_id="Q", source="model"
    )
    assert bad.status == "schema_invalid"
    assert runtime.task_manager.get("Q").tool_calls == 0

    good = runtime.execute_with_recovery(
        "search_memory", {"query": "quota"}, task_id="Q", source="model"
    )
    assert good.success
    assert runtime.task_manager.get("Q").tool_calls == 1
