from __future__ import annotations

import json

import pytest

import memory


def test_legacy_memory_is_migrated_without_data_loss(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "memory.json").write_text(
        json.dumps({"legacy_key": "legacy_value"}),
        encoding="utf-8",
    )

    assert memory.recall("legacy_key")["value"] == "legacy_value"

    document = memory.load_memory_document()
    assert document["schema_version"] == memory.MEMORY_SCHEMA_VERSION
    assert document["records"][0]["kind"] == "fact"
    assert document["records"][0]["source"]["type"] == "legacy"
    assert document["records"][0]["value"] == "legacy_value"


def test_memory_versions_preserve_history_and_invalidate_previous(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    first = memory.remember(
        "architecture",
        "Use XML",
        kind="decision",
        source={"type": "user", "ref": "conversation-1"},
        project_id="launcher",
    )
    second = memory.remember(
        "architecture",
        "Use Compose",
        kind="decision",
        source={"type": "user", "ref": "conversation-2"},
        project_id="launcher",
    )

    assert first["version"] == 1
    assert second["version"] == 2
    current = memory.recall("architecture", project_id="launcher")
    assert current["value"] == "Use Compose"
    assert current["record"]["version"] == 2

    records = memory.load_memory_document()["records"]
    old = next(item for item in records if item["memory_id"] == first["memory_id"])
    new = next(item for item in records if item["memory_id"] == second["memory_id"])
    assert old["valid"] is False
    assert old["invalidated_at"]
    assert old["invalidation_reason"]
    assert new["valid"] is True
    assert new["supersedes"] == first["memory_id"]


def test_memory_scope_prefers_task_then_project_then_global(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    memory.remember("style", "global", source="user")
    memory.remember("style", "project", source="user", project_id="p1")
    memory.remember("style", "task", source="user", project_id="p1", task_id="t1")

    assert memory.recall("style")["value"] == "global"
    assert memory.recall("style", project_id="p1")["value"] == "project"
    assert memory.recall("style", project_id="p1", task_id="t1")["value"] == "task"
    assert memory.recall("style", project_id="p2")["value"] == "global"


def test_search_memory_keeps_project_and_task_scopes_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    memory.remember(
        "android_style",
        "Use Compose",
        kind="preference",
        source="user",
        project_id="launcher",
    )
    memory.remember(
        "backend_style",
        "Use FastAPI",
        kind="preference",
        source="user",
        project_id="backend",
    )
    memory.remember(
        "task_note",
        "Inspect launcher icons",
        kind="experience",
        source={"type": "tool", "ref": "inspection"},
        project_id="launcher",
        task_id="task-1",
    )

    launcher = memory.search_memory(
        "Use",
        project_id="launcher",
    )
    assert all(
        record["project_id"] in (None, "launcher")
        for record in launcher["records"]
    )

    task = memory.search_memory(
        "does-not-match",
        project_id="launcher",
        task_id="task-1",
    )
    assert task["status"] == "success"
    assert task["records"][0]["task_id"] == "task-1"
    assert task["records"][0]["value"] == "Inspect launcher icons"

    backend = memory.search_memory("FastAPI", project_id="launcher")
    assert backend["status"] == "not_found"


def test_memory_invalidation_removes_record_from_default_retrieval(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    memory.remember(
        "obsolete_fact",
        "old information",
        kind="fact",
        source="tool",
        project_id="launcher",
    )
    result = memory.invalidate_memory(
        "obsolete_fact",
        project_id="launcher",
        reason="Repository decision changed",
    )

    assert result["success"] is True
    assert memory.recall("obsolete_fact", project_id="launcher")["status"] == "not_found"

    history = memory.search_memory(
        "obsolete_fact",
        project_id="launcher",
        include_invalid=True,
    )
    assert history["status"] == "success"
    assert history["records"][0]["valid"] is False
    assert history["records"][0]["invalidation_reason"] == "Repository decision changed"


def test_invalid_json_is_not_silently_treated_as_empty_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "memory.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(memory.MemoryStoreError):
        memory.recall("anything")


def test_memory_save_is_atomic_on_replace_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    memory.remember("stable", "original")

    def fail_replace(*args, **kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr(memory.os, "replace", fail_replace)

    with pytest.raises(OSError):
        memory.remember("stable", "replacement")

    assert memory.recall("stable")["value"] == "original"


def test_search_memory_tokenizes_natural_language_queries(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    memory.remember(
        "launcher_project",
        "LauncherOS",
        kind="fact",
        source="user",
        project_id="launcher",
        context=["LauncherOS"],
    )
    memory.remember(
        "architecture_decision",
        "Use Compose with native Android components",
        kind="decision",
        source="user",
        project_id="launcher",
        context=["LauncherOS"],
    )

    result = memory.search_memory("work on LauncherOS", project_id="launcher")

    assert result["status"] == "success"
    assert {record["key"] for record in result["records"]} == {
        "launcher_project",
        "architecture_decision",
    }
