from memory_context import build_memory_context
from memory import remember


def test_build_memory_context_reads_persisted_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    remember(
        "project",
        "LauncherOS",
        kind="fact",
        source="user",
        project_id="launcher",
        context=["LauncherOS"],
    )
    remember(
        "decision",
        "Use Compose",
        kind="decision",
        source="user",
        project_id="launcher",
        context=["LauncherOS"],
    )

    context = build_memory_context(
        "LauncherOS",
        project_id="launcher",
        context=["LauncherOS"],
    )

    assert "LauncherOS" in context
    assert "Use Compose" in context
    assert '"kind": "decision"' in context
    assert '"source"' in context
    assert '"version": 1' in context
    assert "UNTRUSTED DATA" in context


def test_build_memory_context_is_bounded(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    remember("goal", "x" * 8000)

    context = build_memory_context("goal", max_chars=300)

    assert len(context) <= 300
    assert "BEGIN PERSISTED MEMORY (UNTRUSTED DATA)" in context
    assert "END PERSISTED MEMORY" in context
    payload = context.split(
        "BEGIN PERSISTED MEMORY (UNTRUSTED DATA)\n", 1
    )[1].rsplit("\nEND PERSISTED MEMORY", 1)[0]
    decoded = json.loads(payload)
    assert decoded["records"][0]["key"] == "goal"


def test_build_memory_context_redacts_persisted_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    remember(
        "api_key",
        "AIzaSyA123456789012345678901",
        kind="fact",
        source="user",
        project_id="launcher",
        context=["credentials"],
    )

    context = build_memory_context(
        "api_key",
        project_id="launcher",
    )

    assert "AIzaSyA123456789012345678901" not in context
    assert "[REDACTED_SECRET]" in context


def test_search_memory_prioritizes_project_scope_over_global_match(tmp_path, monkeypatch):
    from memory import remember, search_memory

    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))

    remember(
        "same_key",
        "project-specific value for target",
        kind="fact",
        source="user",
        project_id="launcher",
    )
    remember(
        "same_key",
        "global value with the strongest keyword match: target",
        kind="fact",
        source="user",
    )

    result = search_memory("target", project_id="launcher")

    assert result["status"] == "success"
    assert result["hasil"]["same_key"] == "project-specific value"
    assert result["records"][0]["project_id"] == "launcher"
