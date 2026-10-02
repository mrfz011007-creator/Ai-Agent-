from __future__ import annotations

import socket
from pathlib import Path

import pytest

from core.execution_contract import ExecutionContract, ExecutionContractError
from execution.command import run_command
from verification.criteria import CriterionValidationError, validate_criteria


def test_sandbox_allows_workspace_write_but_not_host_tmp(tmp_path):
    host_escape = Path("/tmp/ai-agent-host-escape-test")
    host_escape.unlink(missing_ok=True)

    result = run_command(
        command='python3 -c "from pathlib import Path; Path(\'/tmp/ai-agent-host-escape-test\').write_text(\'x\'); Path(\'inside.txt\').write_text(\'ok\')"',
        cwd=str(tmp_path),
        timeout=10,
    )

    assert result["success"], result
    assert (tmp_path / "inside.txt").read_text() == "ok"
    assert not host_escape.exists()


def test_sandbox_has_no_network():
    result = run_command(
        command='python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect((\'1.1.1.1\', 80))"',
        cwd=".",
        timeout=10,
    )
    assert not result["success"]
    assert result["status"] in {"FAILED", "EXECUTION_ERROR"}
    assert "Network is unreachable" in result["stderr"] or "Errno 101" in result["stderr"]


def test_sandbox_does_not_inherit_secret_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY_1", "TOP-SECRET")
    result = run_command(
        command='python3 -c "import os; raise SystemExit(1 if os.getenv(\'GEMINI_API_KEY_1\') else 0)"',
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result["success"], result


def test_machine_criteria_reject_natural_language():
    with pytest.raises(CriterionValidationError):
        validate_criteria(["APK exists and passes tests"])


def test_machine_criteria_accept_finite_schema():
    criteria = validate_criteria([
        {"type": "all_tasks_completed"},
        {"type": "artifact_kind", "task_id": "build", "kind": "apk"},
    ])
    assert criteria[0]["type"] == "all_tasks_completed"
    assert criteria[1]["kind"] == "apk"


def test_execution_contract_rejects_string_completion_conditions():
    with pytest.raises(ExecutionContractError):
        ExecutionContract(
            objective="build",
            allowed_tools=("run_command",),
            allowed_capabilities=("process.execute",),
            completion_conditions=("build succeeded",),
        )


def test_execution_contract_round_trips_structured_conditions():
    contract = ExecutionContract(
        objective="build",
        allowed_tools=("run_command",),
        allowed_capabilities=("process.execute",),
        completion_conditions=(
            {"type": "tool_success", "task_id": "build", "tool": "run_command"},
        ),
    )
    restored = ExecutionContract.from_dict(contract.to_dict())
    assert restored == contract
