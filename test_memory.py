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



def test_memory_concurrent_writes_preserve_both_updates(monkeypatch, tmp_path):
    import threading
    import time

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))

    import memory
    from memory import load_memory, remember

    original_save = memory._atomic_save_document
    first_save_entered = threading.Event()
    first_call = {"value": True}

    def delayed_save(document):
        if first_call["value"]:
            first_call["value"] = False
            first_save_entered.set()
            time.sleep(0.2)
        return original_save(document)

    monkeypatch.setattr(memory, "_atomic_save_document", delayed_save)

    errors = []

    def writer(key):
        try:
            remember(key, key)
        except Exception as error:
            errors.append(error)

    first = threading.Thread(target=writer, args=("first",))
    first.start()
    assert first_save_entered.wait(2)

    second = threading.Thread(target=writer, args=("second",))
    second.start()

    first.join(3)
    second.join(3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert load_memory() == {"first": "first", "second": "second"}
