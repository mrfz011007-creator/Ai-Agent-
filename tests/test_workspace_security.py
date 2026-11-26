from __future__ import annotations

import os
import threading
import time

import pytest

import tools
from security.workspace import (
    HARD_MAX_FILE_BYTES,
    HARD_MAX_SEARCH_BYTES,
    HARD_MAX_SEARCH_FILES,
    HARD_MAX_SEARCH_RESULTS,
    max_file_bytes,
    max_search_bytes,
    max_search_files,
    max_search_results,
)


def _workspace(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("AI_AGENT_WORKSPACE_ROOT", str(root))
    return root


def test_file_tools_reject_leaf_symlinks_without_touching_target(monkeypatch, tmp_path):
    root = _workspace(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)

    read_result = tools.baca_file("link.txt")
    write_result = tools.tulis_file("link.txt", "overwritten")
    patch_raised = False
    try:
        patch_result = tools.patch_file("link.txt", "outside", "changed")
    except OSError:
        patch_raised = True
        patch_result = None

    assert read_result["success"] is False
    assert write_result["success"] is False
    assert patch_raised is False
    assert patch_result is not None and patch_result["success"] is False
    assert outside.read_text(encoding="utf-8") == "outside-secret"


def test_file_tools_reject_parent_symlink_and_path_traversal(monkeypatch, tmp_path):
    root = _workspace(monkeypatch, tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("outside-secret", encoding="utf-8")
    (root / "linked-dir").symlink_to(outside_dir, target_is_directory=True)

    for name in ("../outside/secret.txt", "linked-dir/secret.txt"):
        assert tools.baca_file(name)["success"] is False
        assert tools.tulis_file(name, "changed")["success"] is False
        result = tools.patch_file(name, "outside", "changed")
        assert result["success"] is False

    assert outside_file.read_text(encoding="utf-8") == "outside-secret"


def test_create_file_and_directory_do_not_follow_parent_symlinks(monkeypatch, tmp_path):
    root = _workspace(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)

    file_result = tools.buat_file("linked/new.txt")
    directory_result = tools.buat_folder("linked/new-dir")

    assert file_result["success"] is False
    assert directory_result["success"] is False
    assert not (outside / "new.txt").exists()
    assert not (outside / "new-dir").exists()


def test_search_skips_symlinked_files_and_directories(monkeypatch, tmp_path):
    root = _workspace(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external.py").write_text("needle-external", encoding="utf-8")
    (root / "inside.py").write_text("needle-inside", encoding="utf-8")
    (root / "linked.py").symlink_to(outside / "external.py")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)

    result = tools.cari_teks("needle", "*.py")

    assert result["success"] is True
    assert [item["path"] for item in result["hasil"]] == ["inside.py"]


def test_file_and_search_limits_are_bounded(monkeypatch, tmp_path):
    root = _workspace(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_AGENT_MAX_FILE_BYTES", "8")
    monkeypatch.setenv("AI_AGENT_MAX_SEARCH_RESULTS", "1")
    monkeypatch.setenv("AI_AGENT_MAX_SEARCH_FILES", "2")
    monkeypatch.setenv("AI_AGENT_MAX_SEARCH_BYTES", "32")

    (root / "large.txt").write_text("0123456789", encoding="utf-8")
    (root / "a.py").write_text("needle-a\n", encoding="utf-8")
    (root / "b.py").write_text("needle-b\n", encoding="utf-8")

    assert tools.baca_file("large.txt")["code"] == "FILE_TOO_LARGE"
    found = tools.cari_teks("needle", "*.py")
    assert len(found["hasil"]) == 1
    assert found["truncated"] is True
    assert found["files_scanned"] <= 2
    assert found["bytes_scanned"] <= 32


def test_environment_overrides_cannot_raise_hard_security_caps(monkeypatch):
    monkeypatch.setenv("AI_AGENT_MAX_FILE_BYTES", str(HARD_MAX_FILE_BYTES * 100))
    monkeypatch.setenv("AI_AGENT_MAX_SEARCH_RESULTS", str(HARD_MAX_SEARCH_RESULTS * 100))
    monkeypatch.setenv("AI_AGENT_MAX_SEARCH_FILES", str(HARD_MAX_SEARCH_FILES * 100))
    monkeypatch.setenv("AI_AGENT_MAX_SEARCH_BYTES", str(HARD_MAX_SEARCH_BYTES * 100))

    assert max_file_bytes() == HARD_MAX_FILE_BYTES
    assert max_search_results() == HARD_MAX_SEARCH_RESULTS
    assert max_search_files() == HARD_MAX_SEARCH_FILES
    assert max_search_bytes() == HARD_MAX_SEARCH_BYTES


def test_search_rejects_unbounded_patterns_and_queries(monkeypatch, tmp_path):
    _workspace(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        tools.cari_teks("needle", "a/" + "*" * 20)
    with pytest.raises(ValueError):
        tools.cari_teks("q" * 4097)


def test_leaf_symlink_swap_race_never_writes_outside_workspace(monkeypatch, tmp_path):
    root = _workspace(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    target = root / "race.txt"
    target.write_text("inside-original", encoding="utf-8")

    stop = threading.Event()

    def swap_path():
        while not stop.is_set():
            try:
                target.unlink(missing_ok=True)
                target.symlink_to(outside)
                time.sleep(0.0002)
                target.unlink(missing_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, b"inside-race")
                finally:
                    os.close(fd)
            except (FileExistsError, FileNotFoundError, OSError):
                continue

    worker = threading.Thread(target=swap_path, daemon=True)
    worker.start()
    try:
        for index in range(200):
            result = tools.tulis_file("race.txt", f"agent-write-{index}")
            assert isinstance(result, dict)
    finally:
        stop.set()
        worker.join(timeout=2)

    assert outside.read_text(encoding="utf-8") == "outside-secret"
