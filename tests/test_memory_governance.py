from __future__ import annotations

import json

import memory


def test_memory_governance_accepts_evidence_backed_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    result = memory.remember(
        "build.failure",
        "Dependency X caused the build failure.",
        memory_type="experience",
        project_id="demo",
        source={"kind": "test", "ref": "evidence-1"},
        provenance={"reason": "verified_test"},
        evidence_refs=["evidence-1"],
    )
    assert result["success"] is True
    record = result["record"]
    assert record["importance"] >= 0.2
    assert record["confidence"] >= 0.35
    assert record["evidence_refs"] == ["evidence-1"]
    assert record["summary"]


def test_memory_governance_rejects_empty_value(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    result = memory.remember(
        "empty",
        None,
        source={"kind": "test", "ref": "evidence-2"},
        provenance={"reason": "test"},
    )
    assert result["success"] is False
    assert result["reason"] == "MEMORY_VALUE_EMPTY"


def test_memory_revision_preserves_history(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    first = memory.remember(
        "decision.build",
        "Use strategy A.",
        memory_type="decision",
        project_id="demo",
        source={"kind": "test", "ref": "evidence-a"},
        provenance={"reason": "test"},
        evidence_refs=["evidence-a"],
    )
    second = memory.update_memory(
        first["record"]["id"],
        "Use strategy B.",
        provenance={"reason": "superseded", "previous_id": first["record"]["id"]},
    )
    assert second["success"] is True
    history = memory.get_memory_history("decision.build", project_id="demo")
    assert history[0]["version"] == 2
    assert history[0]["status"] == "active"
    assert history[1]["version"] == 1
    assert history[1]["status"] == "superseded"


def test_memory_persists_across_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(tmp_path))
    memory.remember(
        "persistent.fact",
        "value",
        source={"kind": "test", "ref": "restart"},
        provenance={"reason": "persistence-test"},
    )
    reloaded = memory.load_memory()
    assert reloaded["schema_version"] == 2
    assert reloaded["records"][0]["key"] == "persistent.fact"
