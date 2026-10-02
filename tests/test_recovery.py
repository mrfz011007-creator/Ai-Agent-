from core.contracts import Task, TaskStatus
from core.task_manager import TaskManager
from core.state_store import StateStore
from core.checkpoint import CheckpointManager
from core.recovery import RecoveryManager, ReconcileOutcome


def test_unknown_state_is_blocked(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R1", "reconcile"))
    task.status = TaskStatus.READY
    manager.start("R1")

    recovery = RecoveryManager(manager)
    recovery.recover_task("R1")

    result = recovery.reconcile("R1", ReconcileOutcome.UNKNOWN, "State is unknown.")
    assert result.status == TaskStatus.BLOCKED
    assert result.action == "BLOCK"


def test_safe_resume_requires_explicit_reason(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = TaskManager(store=store, checkpoints=CheckpointManager(store))
    task = manager.create(Task("R2", "resume"))
    task.status = TaskStatus.READY
    manager.start("R2")

    recovery = RecoveryManager(manager)
    recovery.recover_task("R2")

    result = recovery.reconcile(
        "R2",
        ReconcileOutcome.SAFE_TO_RESUME,
        "Workspace state matches checkpoint.",
    )
    assert result.status == TaskStatus.RUNNING
    assert result.action == "RESUME"
