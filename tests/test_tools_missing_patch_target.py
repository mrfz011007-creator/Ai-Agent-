from __future__ import annotations

import tools


def test_patch_file_returns_structured_error_for_missing_target(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    result = tools.patch_file("missing.py", "old", "new")

    assert result["success"] is False
    assert result["status"] == "error"
    assert result["code"] == "FILE_NOT_FOUND"
