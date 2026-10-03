from memory_context import build_memory_context
from memory import remember


def test_build_memory_context_reads_persisted_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    remember("project", "LauncherOS")
    remember("decision", "Use Compose")

    context = build_memory_context("LauncherOS")

    assert "LauncherOS" in context
    assert "Use Compose" not in context or "project" in context
    assert "UNTRUSTED DATA" in context


def test_build_memory_context_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    remember("goal", "x" * 12000)

    context = build_memory_context("goal", max_chars=100)

    assert len(context) < 300
    assert "[MEMORY_TRUNCATED]" in context
