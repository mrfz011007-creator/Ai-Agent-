from __future__ import annotations

import pytest

import tools


def test_verified_file_writes_reads_and_compares_exact_utf8(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    expected = "Fariz Agent Test\nTahap: 1\nStatus: berhasil"

    result = tools.buat_file_terverifikasi("roundtrip.txt", expected)

    assert result["success"] is True
    assert result["verified"] is True
    assert result["code"] == "CONTENT_MATCH"
    assert result["isi_aktual"] == expected
    assert result["steps"] == {
        "created": True, "written": True, "read_back": True, "exact_match": True
    }
    assert (workspace / "roundtrip.txt").read_bytes() == expected.encode("utf-8")


def test_verified_file_refuses_to_overwrite_existing_file(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    target = workspace / "existing.txt"
    target.write_text("keep original", encoding="utf-8")

    result = tools.buat_file_terverifikasi("existing.txt", "replace me")

    assert result["success"] is False
    assert result["verified"] is False
    assert result["code"] == "FILE_ALREADY_EXISTS"
    assert target.read_text(encoding="utf-8") == "keep original"


def test_verified_file_rejects_workspace_traversal(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    outside = tmp_path / "outside.txt"

    result = tools.buat_file_terverifikasi("../outside.txt", "not allowed")

    assert result["success"] is False
    assert result["code"] == "WORKSPACE_ACCESS_DENIED"
    assert not outside.exists()


def test_verified_file_reports_readback_mismatch(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(tools, "read_file_secure", lambda *_args: b"tampered")

    result = tools.buat_file_terverifikasi("mismatch.txt", "expected")

    assert result["success"] is False
    assert result["verified"] is False
    assert result["code"] == "CONTENT_MISMATCH"
    assert result["isi_aktual"] == "tampered"
    assert result["steps"]["read_back"] is True
    assert result["steps"]["exact_match"] is False


def test_verified_file_rejects_content_over_workspace_limit(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("AI_AGENT_MAX_FILE_BYTES", "8")

    result = tools.buat_file_terverifikasi("large.txt", "123456789")

    assert result["success"] is False
    assert result["code"] == "FILE_TOO_LARGE"
    assert not (workspace / "large.txt").exists()