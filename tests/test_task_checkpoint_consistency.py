import sqlite3

from core.checkpoint import CheckpointManager
from core.contracts import Task, TaskStatus
from core.state_store import StateStore
from core.task_manager import TaskManager


class FailingCheckpointStore(CheckpointManager):
    def capture(self, task, **payload):
        raise sqlite3.OperationalError("checkpoint database unavailable")


def test_persisted_task_transition_survives_checkpoint_failure(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = StateStore(db)
    manager = TaskManager(
        store=store,
        checkpoints=FailingCheckpointStore(store),
    )
    manager.create(Task("checkpoint-failure", "Checkpoint failure"))

    started = manager.start("checkpoint-failure")

    assert started.status == TaskStatus.RUNNING
    restored = StateStore(db).load_task("checkpoint-failure")
    assert restored is not None
    assert restored["status"] == TaskStatus.RUNNING
    assert restored["attempts"] == 1
    assert StateStore(db).load_latest_checkpoint("checkpoint-failure") is None


def test_normal_checkpoint_remains_available(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = StateStore(db)
    manager = TaskManager(
        store=store,
        checkpoints=CheckpointManager(store),
    )
    manager.create(Task("checkpoint-normal", "Normal checkpoint"))

    manager.start("checkpoint-normal")

    checkpoint = store.load_latest_checkpoint("checkpoint-normal")
    assert checkpoint is not None
    assert checkpoint["payload"]["status"] == "RUNNING"
    assert checkpoint["payload"]["attempt"] == 1


def test_rejected_start_does_not_mutate_attempt_counter(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    manager.create(Task("start-guard", "Start guard", status=TaskStatus.READY))
    manager.start("start-guard")

    task = manager.get("start-guard")
    assert task is not None
    assert task.status == TaskStatus.RUNNING
    assert task.attempts == 1

    try:
        manager.start("start-guard")
        assert False, "starting a non-ready task should fail"
    except ValueError as exc:
        assert "invalid transition" in str(exc).lower()

    assert task.status == TaskStatus.RUNNING
    assert task.attempts == 1
    restored = StateStore(tmp_path / "state.sqlite3").load_task("start-guard")
    assert restored["attempts"] == 1
    assert restored["status"] == "RUNNING"
