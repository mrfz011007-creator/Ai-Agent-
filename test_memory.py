"""Tests for persistent long-term memory without import-time side effects."""

def test_memory_round_trip_is_scoped(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    from memory import recall, remember

    result = remember("nama_user", "Fariz")
    assert result["success"] is True

    recalled = recall("nama_user")
    assert recalled["success"] is True
    assert recalled["value"] == "Fariz"
    assert (workspace / "memory.json").exists()


def test_memory_missing_key_is_not_found(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    from memory import recall

    result = recall("warna_favorit")
    assert result["success"] is True
    assert result["status"] == "not_found"
