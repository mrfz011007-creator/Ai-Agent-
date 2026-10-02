import importlib
import json

import pytest


def test_memory_schema_persists_typed_record_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    from memory import load_memory, remember

    result = remember(
        "build_toolchain",
        "Gradle 9.8",
        memory_type="fact",
        project_id="ai-agent",
        task_id="environment-probe",
        context={"platform": "android"},
        source={"kind": "environment_probe", "ref": "probe-001"},
        provenance={"evidence_id": "E-001", "reason": "observed locally"},
        tags=["android", "toolchain"],
    )

    record = result["record"]
    assert record["type"] == "fact"
    assert record["project_id"] == "ai-agent"
    assert record["task_id"] == "environment-probe"
    assert record["source"]["kind"] == "environment_probe"
    assert record["provenance"]["evidence_id"] == "E-001"
    assert record["version"] == 1
    assert record["status"] == "active"

    raw = json.loads((tmp_path / "memory.json").read_text())
    assert raw["schema_version"] == 1
    assert raw["records"][0]["id"] == record["id"]
    assert load_memory()["records"][0]["id"] == record["id"]


def test_memory_update_creates_revision_and_invalidates_previous(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    from memory import get_memory_history, remember, update_memory

    first = remember(
        "model",
        "old-model",
        memory_type="decision",
        project_id="project-a",
        source="planning",
        provenance={"reason": "initial decision"},
    )["record"]

    second = update_memory(
        first["id"],
        "new-model",
        provenance={"reason": "decision changed"},
    )["record"]

    history = get_memory_history("model", project_id="project-a")
    assert second["version"] == 2
    assert second["supersedes_id"] == first["id"]
    assert history[0]["status"] == "active"
    assert history[0]["value"] == "new-model"
    assert history[1]["status"] == "invalidated"
    assert history[1]["value"] == "old-model"


def test_memory_invalidation_removes_record_from_default_retrieval(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    from memory import invalidate_memory, recall, remember, search_memory

    record = remember(
        "temporary",
        "obsolete",
        memory_type="experience",
        source="test",
        provenance={"reason": "test"},
    )["record"]

    assert invalidate_memory(record["id"], reason="superseded")["success"] is True
    assert recall("temporary")["status"] == "not_found"
    assert search_memory("obsolete")["status"] == "not_found"
    assert recall("temporary", include_invalidated=True)["record"]["status"] == "invalidated"


def test_retrieval_is_scoped_by_project_task_and_context(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    from memory import remember, search_memory

    remember(
        "android-build",
        "Use Gradle",
        project_id="launcher",
        task_id="build-debug",
        context={"platform": "android"},
        source="test",
        provenance={"reason": "test"},
    )
    remember(
        "android-build",
        "Use a different toolchain",
        project_id="other",
        task_id="other-task",
        context={"platform": "android"},
        source="test",
        provenance={"reason": "test"},
    )

    result = search_memory(
        "android build",
        project_id="launcher",
        task_id="build-debug",
        context={"platform": "android"},
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["record"]["project_id"] == "launcher"
    assert result["results"][0]["record"]["task_id"] == "build-debug"


def test_memory_survives_module_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    import memory

    memory.remember(
        "restart-check",
        {"ok": True},
        memory_type="fact",
        source="restart-test",
        provenance={"reason": "persistence test"},
    )

    reloaded = importlib.reload(memory)
    result = reloaded.recall("restart-check")
    assert result["success"] is True
    assert result["record"]["value"] == {"ok": True}


def test_legacy_flat_memory_is_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "memory.json").write_text(
        json.dumps({"legacy_key": "legacy_value"}),
        encoding="utf-8",
    )

    from memory import load_memory, recall

    store = load_memory()
    assert store["schema_version"] == 1
    assert store["records"][0]["type"] == "fact"
    assert recall("legacy_key")["value"] == "legacy_value"


def test_invalid_memory_type_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    from memory import MemoryValidationError, remember

    with pytest.raises(MemoryValidationError):
        remember("x", "y", memory_type="unknown")
