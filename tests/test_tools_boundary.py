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
