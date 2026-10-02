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
