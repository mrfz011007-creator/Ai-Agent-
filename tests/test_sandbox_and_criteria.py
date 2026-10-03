from __future__ import annotations

import shlex
import socket
from pathlib import Path

import pytest

from core.execution_contract import ExecutionContract, ExecutionContractError
from execution.command import run_command
from verification.criteria import CriterionValidationError, validate_criteria


def test_sandbox_allows_workspace_write_and_isolates_tmp_when_namespaced(tmp_path):
    # Workspace writes must work in both namespace and hardened modes.
    workspace_script = "from pathlib import Path; Path('inside.txt').write_text('ok')"
    result = run_command(
        command=f"python3 -c {shlex.quote(workspace_script)}",
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result["success"], result
    assert (tmp_path / "inside.txt").read_text() == "ok"

    if result["sandbox_mode"] != "namespace":
        # Hardened fallback does not provide a separate filesystem namespace,
        # so /tmp isolation is intentionally not asserted here.
        return

    # Only namespace mode promises filesystem isolation.
    host_escape = Path("/tmp/ai-agent-host-escape-test")
    host_escape.unlink(missing_ok=True)
    escape_script = "from pathlib import Path; Path('/tmp/ai-agent-host-escape-test').write_text('x')"
    isolated = run_command(
        command=f"python3 -c {shlex.quote(escape_script)}",
        cwd=str(tmp_path),
        timeout=10,
    )
    assert isolated.success is False
    assert not host_escape.exists()


def test_sandbox_has_no_network():
    result = run_command(
        command='python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect((\'1.1.1.1\', 80))"',
        cwd=".",
        timeout=10,
    )
    if result["sandbox_mode"] != "namespace":
        pytest.skip("Kernel namespaces unavailable; hardened fallback cannot enforce network isolation")
    assert not result["success"]
    if result["sandbox_mode"] == "namespace":
        assert result["status"] == "FAILED"
        assert "Network is unreachable" in result["stderr"] or "Errno 101" in result["stderr"]
    else:
        pytest.skip("Kernel namespaces unavailable; hardened fallback cannot enforce network isolation")


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


def test_workspace_file_tools_enforce_size_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_AGENT_MAX_FILE_BYTES", "8")
    (tmp_path / "large.txt").write_text("0123456789", encoding="utf-8")

    from tools import baca_file, cari_teks, patch_file, tulis_file

    assert baca_file("large.txt")["code"] == "FILE_TOO_LARGE"
    assert cari_teks("0", pola="*.txt")["hasil"] == []
    assert patch_file("large.txt", "0", "x")["success"] is False
    assert tulis_file("new.txt", "0123456789")["code"] == "FILE_TOO_LARGE"


def test_workspace_file_tools_check_existing_size_before_snapshot_read(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_AGENT_MAX_FILE_BYTES", "8")
    (tmp_path / "large.txt").write_text("0123456789", encoding="utf-8")

    from tools import patch_file, tulis_file

    assert patch_file(
        "large.txt", "0", "x", expected_sha256="unused"
    )["code"] == "FILE_TOO_LARGE"
    assert tulis_file(
        "large.txt", "ok", expected_sha256="unused"
    )["code"] == "FILE_TOO_LARGE"


def test_command_timeout_redacts_secret_output(tmp_path):
    result = run_command(
        command=(
            'python3 -c "import time; '
            'print(\\\"GEMINI_API_KEY_1=TOP-SECRET-VALUE\\\", flush=True); '
            'time.sleep(2)"'
        ),
        cwd=str(tmp_path),
        timeout=0.2,
    )
    assert result["status"] == "TIMEOUT"
    assert "TOP-SECRET-VALUE" not in result["stdout"]
    assert "REDACTED" in result["stdout"]


def test_termux_uses_hardened_fallback_before_namespace_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")

    from execution.sandbox import SandboxUnavailable, prepare_sandbox

    with pytest.raises(SandboxUnavailable):
        prepare_sandbox(["python3", "-V"], cwd=str(tmp_path))


def test_sandbox_cleanup_removes_launcher_for_mkdtemp_style_root(tmp_path):
    from execution.sandbox import cleanup_sandbox

    token = "a" * 32
    root = tmp_path / f".ai-agent-root-{token}-random"
    root.mkdir()
    launcher = tmp_path / f".ai-agent-launcher-{token}.sh"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    cleanup_sandbox(str(root))

    assert not root.exists()
    assert not launcher.exists()


def test_sandbox_cleanup_ignores_malformed_root_name(tmp_path):
    from execution.sandbox import cleanup_sandbox

    root = tmp_path / ".ai-agent-root-not-a-token-random"
    root.mkdir()
    launcher = tmp_path / ".ai-agent-launcher-not-a-token.sh"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    cleanup_sandbox(str(root))

    assert not root.exists()
    assert launcher.exists()
