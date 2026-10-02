from __future__ import annotations

from pathlib import Path

import pytest

import tools


def test_legacy_tools_follow_configured_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    assert tools.path_aman("src/main.py") == workspace / "src" / "main.py"

    tools.buat_folder("src")
    tools.buat_file("src/main.py")
    tools.tulis_file("src/main.py", "print('ok')")

    assert (workspace / "src" / "main.py").read_text(encoding="utf-8") == "print('ok')"


def test_legacy_tools_reject_workspace_escape(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    with pytest.raises(PermissionError):
        tools.path_aman("../outside.txt")

    with pytest.raises(PermissionError):
        tools.path_aman(str(workspace.parent / "outside.txt"))


def test_legacy_python_tool_rejects_non_python(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    (workspace / "script.txt").write_text("print('no')", encoding="utf-8")

    result = tools.jalankan_python("script.txt")

    assert result["success"] is False
    assert "Python" in result["pesan"]


def test_legacy_command_uses_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    result = tools.lokasi()

    assert result["success"] is True
    assert Path(result["stdout"].strip()).resolve() == workspace.resolve()


def test_memory_is_scoped_to_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    from memory import recall, remember

    remember("scope", "workspace-only")

    assert (workspace / "memory.json").exists()
    assert recall("scope")["value"] == "workspace-only"


def test_command_output_is_redacted():
    from execution.command import run_command

    result = run_command(
        command="python -c \"print('AIzaSyA12345678901234567890')\"",
        cwd=".",
    )

    assert result["success"] is True
    assert "AIzaSyA12345678901234567890" not in result["stdout"]
    assert "[REDACTED_SECRET]" in result["stdout"]


def test_search_and_exact_patch_are_workspace_bounded(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    from tools import cari_teks, patch_file

    target = workspace / "sample.py"
    target.write_text("alpha\nbeta\nbeta\n", encoding="utf-8")

    snapshot = tools.baca_file("sample.py")
    assert snapshot["success"] is True
    assert len(snapshot["sha256"]) == 64

    target.write_text("alpha\nchanged\nbeta\n", encoding="utf-8")
    stale = patch_file("sample.py", "beta", "gamma", expected_count=1, expected_sha256=snapshot["sha256"])
    assert stale["success"] is False
    assert stale["code"] == "FILE_CHANGED"

    target.write_text("alpha\nbeta\nbeta\n", encoding="utf-8")

    found = cari_teks("beta")
    assert found["success"] is True
    assert [item["line"] for item in found["hasil"]] == [2, 3]

    rejected = patch_file("sample.py", "beta", "gamma", expected_count=1)
    assert rejected["success"] is False
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\nbeta\n"

    applied = patch_file("sample.py", "beta", "gamma", expected_count=2, expected_sha256=snapshot["sha256"])
    assert applied["success"] is True
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\ngamma\n"


def test_write_rejects_stale_snapshot(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    target = workspace / "sample.py"
    target.write_text("original\n", encoding="utf-8")
    snapshot = tools.baca_file("sample.py")

    target.write_text("external-change\n", encoding="utf-8")
    result = tools.tulis_file("sample.py", "agent-change\n", expected_sha256=snapshot["sha256"])

    assert result["success"] is False
    assert result["code"] == "FILE_CHANGED"
    assert target.read_text(encoding="utf-8") == "external-change\n"


def test_search_does_not_follow_workspace_escape(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret-marker", encoding="utf-8")
    (workspace / "inside.py").write_text("inside-marker", encoding="utf-8")
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    from tools import cari_teks

    assert cari_teks("secret-marker")["hasil"] == []


def test_tool_router_bounds_total_structured_output(monkeypatch, tmp_path):
    from core.budget import Budget, BudgetManager
    from core.contracts import ToolRequest
    from execution.router import ToolRouter
    from security.policy import PolicyEngine
    from verification.evidence import EvidenceStore

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    registry = {"huge": {"func": lambda: {"items": ["x" * 20 for _ in range(20)]}, "permission": "safe"}}
    budget = BudgetManager(Budget(max_output_chars=100))
    router = ToolRouter(
        registry_getter=registry.get,
        policy=PolicyEngine(registry.get),
        budget=budget,
        evidence=EvidenceStore(),
    )

    result = router.execute(ToolRequest(tool="huge", action="execute", arguments={}))

    assert result.success is True
    assert result.data["output_truncated"] is True
    assert sum(len(item) for item in result.data["items"]) <= 100
