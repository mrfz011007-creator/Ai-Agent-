import pytest

from core.contracts import Task, TaskStatus, VerificationResult, VerificationStatus
from core.state_store import StateStore
from core.task_manager import TaskManager


def _manager(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    return TaskManager(store=store), store


def test_start_rolls_back_memory_when_persistence_fails(tmp_path):
    manager, store = _manager(tmp_path)
    manager.create(Task("T1", "start"))

    def fail_save(*args, **kwargs):
        raise OSError("simulated sqlite failure")

    store.save_task = fail_save
    task = manager.get("T1")

    with pytest.raises(OSError):
        manager.start("T1")

    assert task.status == TaskStatus.PENDING
    assert task.attempts == 0
    saved = StateStore(tmp_path / "state.sqlite3").load_task("T1")
    assert saved["status"] == TaskStatus.PENDING
    assert saved["attempts"] == 0


def test_begin_verification_rolls_back_when_persistence_fails(tmp_path):
    manager, store = _manager(tmp_path)
    manager.create(Task("T2", "verify"))
    manager.get("T2").status = TaskStatus.READY
    manager.start("T2")

    def fail_save(*args, **kwargs):
        raise OSError("simulated sqlite failure")

    store.save_task = fail_save
    task = manager.get("T2")

    with pytest.raises(OSError):
        manager.begin_verification("T2")

    assert task.status == TaskStatus.RUNNING
    saved = StateStore(tmp_path / "state.sqlite3").load_task("T2")
    assert saved["status"] == TaskStatus.RUNNING


def test_completion_rolls_back_when_persistence_fails(tmp_path):
    manager, store = _manager(tmp_path)
    manager.create(Task("T3", "complete"))
    manager.get("T3").status = TaskStatus.READY
    manager.start("T3")
    manager.begin_verification("T3")

    verification = VerificationResult(
        VerificationStatus.PASSED,
        "verified",
        ("E1",),
        "acceptance_gate",
    )

    # Completion performs evidence validation before mutating the task.
    original_load_evidence = store.load_evidence
    store.load_evidence = lambda evidence_id: {
        "task_id": "T3",
        "success": True,
        "attempt_id": "T3:attempt:1",
    }

    def fail_save(*args, **kwargs):
        raise OSError("simulated sqlite failure")

    store.save_task = fail_save
    task = manager.get("T3")

    with pytest.raises(OSError):
        manager.complete("T3", verification)

    assert task.status == TaskStatus.VERIFYING
    assert task.result is None
    saved = StateStore(tmp_path / "state.sqlite3").load_task("T3")
    assert saved["status"] == TaskStatus.VERIFYING

    store.load_evidence = original_load_evidence


def test_failed_transition_rolls_back_when_persistence_fails(tmp_path):
    manager, store = _manager(tmp_path)
    manager.create(Task("T4", "fail"))
    manager.get("T4").status = TaskStatus.READY
    manager.start("T4")

    def fail_save(*args, **kwargs):
        raise OSError("simulated sqlite failure")

    store.save_task = fail_save
    task = manager.get("T4")

    with pytest.raises(OSError):
        manager.fail("T4", "boom")

    assert task.status == TaskStatus.RUNNING
    assert task.result is None
    saved = StateStore(tmp_path / "state.sqlite3").load_task("T4")
    assert saved["status"] == TaskStatus.RUNNING


def test_tool_call_counter_rolls_back_when_persistence_fails(tmp_path):
    manager, store = _manager(tmp_path)
    manager.create(Task("T5", "tool"))
    task = manager.get("T5")

    def fail_save(*args, **kwargs):
        raise OSError("simulated sqlite failure")

    store.save_task = fail_save

    with pytest.raises(OSError):
        manager.consume_tool_call("T5")

    assert task.tool_calls == 0
    saved = StateStore(tmp_path / "state.sqlite3").load_task("T5")
    assert saved["payload"]["tool_calls"] == 0


def test_create_removes_memory_entry_when_initial_persistence_fails(tmp_path):
    manager, store = _manager(tmp_path)

    def fail_save(*args, **kwargs):
        raise OSError("simulated sqlite failure")

    store.save_task = fail_save

    with pytest.raises(OSError):
        manager.create(Task("T6", "create"))

    assert manager.get("T6") is None
    assert StateStore(tmp_path / "state.sqlite3").load_task("T6") is None


def test_completion_rejects_evidence_from_another_attempt(tmp_path):
    manager, store = _manager(tmp_path)
    manager.create(Task("T7", "attempt-bound completion"))
    manager.get("T7").status = TaskStatus.READY
    manager.start("T7")
    manager.begin_verification("T7")

    verification = VerificationResult(
        VerificationStatus.PASSED,
        "verified",
        ("E-OLD",),
        "acceptance_gate",
    )

    store.load_evidence = lambda evidence_id: {
        "task_id": "T7",
        "success": True,
        "attempt_id": "T7:attempt:0",
    }

    with pytest.raises(ValueError, match="another attempt"):
        manager.complete("T7", verification)

    assert manager.get("T7").status == TaskStatus.VERIFYING
