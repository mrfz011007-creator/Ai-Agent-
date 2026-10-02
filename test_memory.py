def test_memory_save_and_recall_are_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    from memory import recall, remember

    assert remember("nama_user", "Fariz")["success"] is True
    assert recall("nama_user")["value"] == "Fariz"


def test_memory_missing_key_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    from memory import recall

    result = recall("warna_favorit")
    assert result["status"] == "not_found"
    assert result["success"] is True
